"""Deterministic checker. Reads only canonical trajectory fields, never raw_history.

Beyond answer text, the evaluator requires observation grounding:
the trajectory must contain the task's required tool calls, each cited
number must appear numerically in a data-tool observation (policy text
does not count), and the observed finances must support the verdict.
Rule-based only; no LLM judge.
"""

import json
import re

# Hedged verdicts never count as a match, either way.
HEDGES = (
    "may qualif", "might qualif", "could qualif",
    "may be eligib", "might be eligib", "could be eligib",
    "may be", "might be", "could be",
    "may or may not", "might or might not",
    "possibly qualif", "possibly eligib",
    "unclear whether", "uncertain whether", "not sure whether",
)

POSITIVE = (
    "qualif", "eligib",
    "waiver applies", "waiver granted", "waivers apply", "waiver approved",
    "fee is waived", "fee waived", "fees waived", "is waived", "are waived",
)

NEGATIVE = (
    "does not qualif", "do not qualif", "doesn't qualif", "doesnt qualif",
    "not qualif", "never qualif",
    "not eligib", "ineligib",
    "waiver does not apply", "waiver doesn't apply", "waiver do not apply",
    "waiver is denied", "waiver denied", "waiver rejected",
    "no waiver", "without waiver", "not entitled",
    "not waived", "isn't waived", "isnt waived", "never waived", "n't waived",
)

# Observations from these tools may ground numeric evidence. Policy text
# is excluded: quoting the policy must never count as examining finances.
# `calculate` is deterministic, so a value it produced (avg of [1800, 1200] ->
# 1500) is grounded evidence even though it appears in no raw transaction.
DATA_TOOLS = ("get_account", "get_transactions", "calculate")


def _evidence_present(evidence, text):
    """True when the final answer cites the required evidence.

    Non-numeric evidence (policy ids, rule names) is a plain substring test.
    Numeric evidence is compared by value, so a number the model formats as
    "$1,500" or "1500.0" still counts. The value itself must still be grounded
    by the separate grounded_numbers check against data-tool observations; this
    only stops formatting from being mistaken for a missing citation.
    """
    if not _is_numberish(evidence):
        return evidence.lower() in text
    if evidence.lower() in text:
        return True
    try:
        want = float(str(evidence).replace(",", ""))
    except ValueError:
        return evidence.lower() in text
    for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        try:
            if float(m.replace(",", "")) == want:
                return True
        except ValueError:
            continue
    return False


def _is_numberish(evidence):
    return bool(re.fullmatch(r"\s*-?\d[\d,]*(?:\.\d+)?\s*", str(evidence)))


def check_expected_answer(spec, text):
    """Deterministic final-answer check. Operates on answer text only.

    spec is {"type": "number", "value": N}, {"type": "enum", "values": [...],
    "expected": X}, or {"type": "text", "contains": [...]}. Unknown types are
    rejected by validate_task, so falling through to False here is unreachable
    for valid tasks.
    # ponytail: float == on parsed decimals; epsilon matching if a task ever needs it.
    """
    kind = spec.get("type")
    if kind == "number":
        want = float(spec["value"])
        return any(float(m.replace(",", "")) == want
                   for m in re.findall(r"-?\d[\d,]*(?:\.\d+)?", text))
    if kind == "enum":
        return text.strip().lower() == str(spec["expected"]).strip().lower()
    if kind == "text":
        return all(_evidence_present(e, text) for e in spec["contains"])
    return False


def _verdict_ok(expected_eligible, text):
    if any(h in text for h in HEDGES):
        return False
    pos = any(p in text for p in POSITIVE)
    neg = any(n in text for n in NEGATIVE)
    if expected_eligible:
        return pos and not neg
    return neg


def _steps_of(trajectory):
    steps = trajectory.get("steps", []) if isinstance(trajectory, dict) else trajectory.steps
    return [s if isinstance(s, dict) else {"action": s.action, "arguments": s.arguments, "observation": s.observation}
            for s in (steps or [])]


def _observed_numbers(steps):
    """All numeric values in data-tool observations (policy excluded)."""
    found = set()
    for s in steps:
        if s.get("action") not in DATA_TOOLS:
            continue
        for m in re.findall(r"-?\d+(?:\.\d+)?", json.dumps(s.get("observation"), default=str)):
            found.add(float(m))
    return found


def _observed_finances(task, steps):
    """Average balance and 30d direct deposits for the task's customer,
    rebuilt strictly from data-tool observations."""
    customer = task["customer_id"]
    owned = set()
    balances = []
    for s in steps:
        if s.get("action") != "get_account":
            continue
        obs = s.get("observation")
        accts = obs if isinstance(obs, list) else []
        for a in accts:
            if isinstance(a, dict) and a.get("customer_id") == customer:
                owned.add(a.get("account_id"))
                if isinstance(a.get("balance"), (int, float)):
                    balances.append(float(a["balance"]))
    deposits = 0.0
    for s in steps:
        if s.get("action") != "get_transactions":
            continue
        obs = s.get("observation")
        txns = obs if isinstance(obs, list) else []
        acct = (s.get("arguments") or {}).get("account_id")
        if acct not in owned:
            continue
        for t in txns:
            if (isinstance(t, dict) and t.get("type") == "direct_deposit"
                    and isinstance(t.get("days_ago"), (int, float)) and t["days_ago"] <= 30
                    and isinstance(t.get("amount"), (int, float))):
                deposits += float(t["amount"])
    avg = sum(balances) / len(balances) if balances else None
    return avg, deposits


def _parse_thresholds(steps):
    """Balance/deposit thresholds from observed policy text. None if unparseable
    (support check is then skipped rather than failed)."""
    for s in steps:
        if s.get("action") != "search_policy":
            continue
        obs = s.get("observation")
        policies = obs if isinstance(obs, list) else []
        for p in policies:
            if not isinstance(p, dict):
                continue
            text = str(p.get("text", ""))
            bal = re.search(r"RULE-A[^\$]{0,200}\$\s*([\d,]+)", text)
            dep = re.search(r"RULE-B[^\$]{0,200}\$\s*([\d,]+)", text)
            if bal and dep:
                return float(bal.group(1).replace(",", "")), float(dep.group(1).replace(",", ""))
    return None


def evaluate(task, trajectory):
    """Return passed/reasons/verdict_match/evidence_match/grounding_ok/support_ok."""
    if isinstance(trajectory, dict):
        final = trajectory.get("final_answer")
        term = trajectory.get("termination_reason", "")
    else:
        trajectory
        final = trajectory.final_answer
        term = trajectory.termination_reason
    steps = _steps_of(trajectory)
    reasons = []
    if term != "agent_final":
        reasons.append(f"bad termination: {term}")
    if not isinstance(final, str) or not final.strip():
        reasons.append("missing final answer")
        return {"passed": False, "reasons": reasons, "verdict_match": False,
                "evidence_match": False, "grounding_ok": False, "support_ok": False}

    text = final.lower()
    # ponytail: waiver phrase check stays the default; expected_answer overrides
    # it per task. Grounding/support below are untouched, so a cited number is
    # still ungrounded unless a data-tool observation contains it.
    if task.get("expected_answer") is None:
        verdict_match = _verdict_ok(task["expected_eligible"], text)
    else:
        verdict_match = check_expected_answer(task["expected_answer"], text)
    if not verdict_match:
        reasons.append("verdict mismatch")

    missing = [e for e in task.get("required_evidence", []) if not _evidence_present(e, text)]
    if task.get("expected_rule", "none") != "none" and task["expected_rule"].lower() not in text:
        missing.append(f"rule citation: {task['expected_rule']}")
    evidence_match = not missing
    if missing:
        reasons.append(f"missing evidence: {missing}")

    # Grounding: required tools used, cited numbers present in data observations.
    grounding_ok = True
    used = {s.get("action") for s in steps}
    missing_tools = [t for t in task.get("required_tools", []) if t not in used]
    if missing_tools:
        grounding_ok = False
        reasons.append(f"missing required tool calls: {missing_tools}")
    observed = _observed_numbers(steps)
    ungrounded = [n for n in task.get("grounded_numbers", []) if float(n) not in observed]
    if ungrounded:
        grounding_ok = False
        reasons.append(f"ungrounded numbers (absent from data observations): {ungrounded}")

    # Support: observed finances must back the expected verdict. Waiver-domain
    # only; runs solely when the task opts in via "support": "waiver".
    # ponytail: single hardcoded domain check, no rule engine. More domains
    # later means more explicit opt-in values, not a generic DSL.
    support_ok = True
    thresholds = _parse_thresholds(steps) if task.get("support") == "waiver" else None
    if thresholds is not None:
        bal_t, dep_t = thresholds
        avg, deposits = _observed_finances(task, steps)
        supported = (avg is not None and avg >= bal_t) or deposits >= dep_t
        if supported != task["expected_eligible"]:
            support_ok = False
            avg_s = f"{avg:.2f}" if avg is not None else "unobserved"
            reasons.append(
                f"observations do not support verdict (avg_balance={avg_s} vs {bal_t}, "
                f"deposits_30d={deposits} vs {dep_t})"
            )

    evidence_match = evidence_match and grounding_ok and support_ok
    passed = verdict_match and evidence_match and term == "agent_final"
    return {"passed": passed, "reasons": reasons, "verdict_match": verdict_match,
            "evidence_match": evidence_match, "grounding_ok": grounding_ok, "support_ok": support_ok}
