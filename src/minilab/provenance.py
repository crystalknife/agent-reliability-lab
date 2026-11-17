"""Execution provenance: what a stored trial record does and does not show.

Read-only analysis of one trial (a trajectory dict as written to JSONL, or a
Trajectory). Nothing here changes a record, a score, or the agent loop.

The point of the module is to keep apart things that are easy to merge:

* PROPOSED calls: tool calls present in the model responses that the harness
  received (from `raw_history`).
* EMITTED calls: tool calls the model itself produced. The harness only sees
  what the adapter returns, so emitted == proposed is known ONLY when the
  caller states that adapter capture is complete. Otherwise the proposed
  count is reported as a lower bound and the emitted count is
  `indeterminate`.
* EXECUTED calls: calls the harness dispatched to the environment and
  recorded as a step. A proposed call is never assumed to have executed.
* DISCARDED calls: proposed but not dispatched, with a reason code.
* FAILED calls: executed calls whose observation is an error.
* RETRIES: not visible in a trial record at all. Reported `not_observable`
  unless the record carries an explicit `adapter_retries` integer.

Every reported quantity is an Observed(value, status, reason) where status
is one of:
  verified        the record establishes the value
  indeterminate   the record is compatible with more than one value
  not_observable  the record cannot show this at all
`reason` says why whenever the status is not `verified`.

Iterations versus calls: one iteration is one model response. In
"sequential" mode at most the first proposed call of a response is executed;
in "batch" mode every proposed call is executed in order until one raises.
The execution mode is not stored in a trajectory, so it must be passed in;
if it is not, discard reasons are left unclassified.

No budget is enforced here. `BudgetPolicy` is the plug-in point for a future
rule; the default records that no policy is configured.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Optional


VERIFIED = "verified"
INDETERMINATE = "indeterminate"
NOT_OBSERVABLE = "not_observable"
STATUSES = (VERIFIED, INDETERMINATE, NOT_OBSERVABLE)

EXEC_MODES = ("sequential", "batch")
LOOKUP_TOOLS = ("get_customer", "get_account", "get_transactions", "search_policy")
PSEUDO_ACTIONS = ("model_error", "invalid_action")

# argument name -> identifier type, for the key argument of a lookup
IDENTIFIER_ARGUMENTS = {"customer_id": "customer", "account_id": "account", "query": "policy_query"}
# observation key -> identifier type
IDENTIFIER_FIELDS = {"customer_id": "customer", "account_id": "account",
                     "transaction_id": "transaction", "policy_id": "policy"}

DISCARD_SEQUENTIAL_EXTRA = "sequential_extra"
DISCARD_AFTER_TERMINAL_ERROR = "not_dispatched_after_terminal_error"
DISCARD_UNCLASSIFIED = "unclassified"
DISCARD_ADAPTER_DEFERRED = "adapter_deferred"   # the adapter returned it as a deferred call, never dispatched

# What each adapter hands the harness, established by READING the adapter
# code on 2026-11-16 (no provider was called). `capture`:
#   by_construction     offline model that returns exactly one call per response
#   all_calls_returned  every call of the first choice is returned (as "calls" or as
#                       "deferred_tool_calls"); not verified against a live SDK
#   first_call_only     only the first call is returned; further calls the model
#                       emitted are dropped before the harness sees them
#   foreign             LangChain message history; not reconstructed here
# `retries`: none of the adapters sets or records a retry count, and SDK
# defaults apply, so retries are not observable for any real provider.
ADAPTER_CAPABILITIES = {
    "local-stub": {"capture": "by_construction", "retries": "none_possible"},
    "local": {"capture": "all_calls_returned", "retries": "not_observable"},
    "gemini": {"capture": "all_calls_returned", "retries": "not_observable"},
    "omniroute": {"capture": "first_call_only", "retries": "not_observable"},   # wraps the openai adapter
    "openai": {"capture": "first_call_only", "retries": "not_observable"},
    "groq": {"capture": "first_call_only", "retries": "not_observable"},
    "openrouter": {"capture": "first_call_only", "retries": "not_observable"},
    "langchain": {"capture": "foreign", "retries": "not_observable"},
}


def capture_completeness_for(provider):
    """Value for analyze_trial(adapter_capture_complete=...): True only for
    the offline models, False where the adapter is known to drop calls, and
    None (unknown) everywhere else, including adapters that appear to return
    every call, because that has only been read, not observed."""
    capture = ADAPTER_CAPABILITIES.get(provider, {}).get("capture")
    if capture == "by_construction":
        return True
    if capture == "first_call_only":
        return False
    return None


@dataclass(frozen=True)
class Observed:
    value: object
    status: str
    reason: str = ""

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError(f"unknown status {self.status!r}")
        if self.status != VERIFIED and not self.reason:
            raise ValueError("a non-verified value must say why")


def verified(value):
    return Observed(value, VERIFIED)


@dataclass(frozen=True)
class CallEvent:
    """One proposed tool call and what happened to it."""
    iteration: int            # 1-based model-response index
    position: int             # 0-based position inside that response
    tool: str
    executed: bool
    failed: bool = False      # executed and the observation is an error
    discard_reason: str = ""  # set when not executed
    is_lookup: bool = False


@dataclass(frozen=True)
class TrialProvenance:
    run_id: str
    task_id: str
    experiment_id: str
    exec_mode: Observed
    history_format: str
    model_response_iterations: Observed
    model_requests_failed: Observed
    calls_proposed: Observed
    calls_emitted: Observed
    calls_executed: Observed
    calls_discarded: Observed
    calls_failed: Observed
    lookup_calls_proposed: Observed
    lookup_calls_executed: Observed
    lookup_calls_discarded: Observed
    lookup_calls_failed: Observed
    lookup_empty_results: Observed
    retries: Observed
    distinct_identifiers_attempted: Observed
    distinct_identifiers_observed: Observed
    distinct_identifiers_proposed_only_in_discarded_calls: Observed
    identifier_emission_events: Observed
    identifier_use_events: Observed
    identifier_use_events_executed: Observed
    accounting_identity: Observed
    discard_reasons: dict = field(default_factory=dict)
    events: tuple = ()

    def to_dict(self, include_events=False):
        d = asdict(self)
        if not include_events:
            d.pop("events")
        return d

    def completeness(self):
        """Counts of fields by status: a quick 'how much do we actually know'."""
        out = {s: 0 for s in STATUSES}
        for v in self.__dict__.values():
            if isinstance(v, Observed):
                out[v.status] += 1
        return out


def _as_dict(obj):
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    raise TypeError(f"expected a trajectory dict or Trajectory, got {type(obj).__name__}")


def _proposed_calls(content):
    """Calls in one assistant message as the harness received it. Returns
    (calls, well_formed). Mirrors agent._as_call_list without importing it,
    so a change there shows up as an accounting residual instead of being
    silently followed."""
    if isinstance(content, dict) and isinstance(content.get("calls"), list) and content["calls"]:
        raw = content["calls"]
    elif isinstance(content, dict) and "tool" in content and isinstance(content.get("arguments", {}), dict):
        raw = [content]
    else:
        return [], False
    calls = []
    for c in raw:
        if not isinstance(c, dict) or "tool" not in c or not isinstance(c.get("arguments", {}), dict):
            return [], False
        calls.append({"tool": c["tool"], "arguments": c.get("arguments", {}) or {}})
    # The Gemini adapter returns the first call plus the others it received
    # under "deferred_tool_calls". They were proposed by the model and never
    # dispatched; count them instead of losing them.
    deferred = content.get("deferred_tool_calls") if isinstance(content, dict) else None
    if isinstance(deferred, list):
        for c in deferred:
            if isinstance(c, dict) and "tool" in c and isinstance(c.get("arguments", {}), dict):
                calls.append({"tool": c["tool"], "arguments": c.get("arguments", {}) or {},
                              "deferred": True})
    return calls, True


def _same_call(call, step):
    return call["tool"] == step.get("action") and (call["arguments"] or {}) == (step.get("arguments") or {})


def _is_error(observation):
    return isinstance(observation, dict) and "error" in observation


def _identifier_uses(tool, arguments):
    if tool not in LOOKUP_TOOLS:
        return []
    out = []
    for arg, kind in IDENTIFIER_ARGUMENTS.items():
        v = arguments.get(arg)
        if isinstance(v, str) and v:
            out.append((kind, v))
    return out


def _identifier_emissions(observation):
    """(type, value) for every identifier-valued field in an observation."""
    out = []

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in IDENTIFIER_FIELDS and isinstance(v, str) and v:
                    out.append((IDENTIFIER_FIELDS[k], v))
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(observation)
    return out


def analyze_trial(record, *, exec_mode=None, adapter_capture_complete=None):
    """Build a TrialProvenance from one stored trial.

    exec_mode: "sequential" | "batch" | None (None = not recorded).
    adapter_capture_complete: True if the caller knows the adapter returned
      every call the model emitted; False if it is known not to; None if
      unknown (the default, and the honest value for every existing adapter:
      their bodies have not been audited for this).
    """
    if exec_mode is not None and exec_mode not in EXEC_MODES:
        raise ValueError(f"unknown exec_mode {exec_mode!r}")
    rec = _as_dict(record)
    steps = [s if isinstance(s, dict) else asdict(s) for s in rec.get("steps", [])]
    history = rec.get("raw_history") or []
    common = dict(run_id=str(rec.get("run_id", "")), task_id=str(rec.get("task_id", "")),
                  experiment_id=str(rec.get("experiment_id", "")))
    mode = (verified(exec_mode) if exec_mode else
            Observed(None, NOT_OBSERVABLE, "exec_mode is not stored in a trajectory and was not supplied"))

    real_steps = [s for s in steps if s.get("action") not in PSEUDO_ACTIONS]
    failed_requests = sum(1 for s in steps if s.get("action") == "model_error")
    executed_n = len(real_steps)
    failed_n = sum(1 for s in real_steps if _is_error(s.get("observation")))
    lookups_exec = [s for s in real_steps if s.get("action") in LOOKUP_TOOLS]
    empty_n = sum(1 for s in lookups_exec if s.get("observation") == [])
    attempted = {u for s in lookups_exec for u in _identifier_uses(s["action"], s.get("arguments") or {})}
    emissions = [e for s in real_steps for e in _identifier_emissions(s.get("observation"))]
    retries = (verified(rec["adapter_retries"])
               if isinstance(rec.get("adapter_retries"), int) and not isinstance(rec.get("adapter_retries"), bool)
               else Observed(None, NOT_OBSERVABLE,
                             "the record has no adapter_retries field; SDK/adapter retries are not "
                             "visible to the harness and are not assumed to be zero"))

    harness_history = bool(history) and all(isinstance(m, dict) and "role" in m for m in history)
    if not harness_history:
        why = ("raw_history is absent" if not history else
               "raw_history is not in the MiniLab harness format (e.g. a LangChain message list)")
        gone = Observed(None, NOT_OBSERVABLE, why + "; proposed and discarded calls cannot be reconstructed")
        return TrialProvenance(
            **common, exec_mode=mode, history_format="absent" if not history else "foreign",
            model_response_iterations=gone, model_requests_failed=verified(failed_requests),
            calls_proposed=gone, calls_emitted=gone, calls_executed=verified(executed_n),
            calls_discarded=gone, calls_failed=verified(failed_n),
            lookup_calls_proposed=gone, lookup_calls_executed=verified(len(lookups_exec)),
            lookup_calls_discarded=gone,
            lookup_calls_failed=verified(sum(1 for s in lookups_exec if _is_error(s.get("observation")))),
            lookup_empty_results=verified(empty_n), retries=retries,
            distinct_identifiers_attempted=verified(len(attempted)),
            distinct_identifiers_observed=verified(len(set(emissions))),
            distinct_identifiers_proposed_only_in_discarded_calls=gone,
            identifier_emission_events=verified(len(emissions)),
            identifier_use_events=gone,
            identifier_use_events_executed=verified(
                sum(len(_identifier_uses(s["action"], s.get("arguments") or {})) for s in lookups_exec)),
            accounting_identity=gone)

    assistant = [m for m in history if m.get("role") == "assistant"]
    events = []
    residual = 0
    consumed = set()
    for it, msg in enumerate(assistant, start=1):
        calls, ok = _proposed_calls(msg.get("content"))
        if not ok:
            continue                       # a final answer or a malformed response proposes no call
        here = [(i, s) for i, s in enumerate(steps)
                if s.get("step") == it and s.get("action") not in PSEUDO_ACTIONS and i not in consumed]
        for pos, call in enumerate(calls):
            # Steps are matched in order; once a dispatch raises the loop
            # ends, so later calls of the response simply have no step.
            match = (None if call.get("deferred") else
                     next(((i, s) for i, s in here if i not in consumed and _same_call(call, s)), None))
            is_lookup = call["tool"] in LOOKUP_TOOLS
            if call.get("deferred"):
                events.append(CallEvent(it, pos, call["tool"], False, False, DISCARD_ADAPTER_DEFERRED, is_lookup))
                continue
            if match is not None:
                consumed.add(match[0])
                failed = _is_error(match[1].get("observation"))
                events.append(CallEvent(it, pos, call["tool"], True, failed, "", is_lookup))
                continue
            if exec_mode == "sequential" and pos > 0:
                reason = DISCARD_SEQUENTIAL_EXTRA
            elif exec_mode == "batch" and pos > 0:
                reason = DISCARD_AFTER_TERMINAL_ERROR
            else:
                reason = DISCARD_UNCLASSIFIED
                if exec_mode is not None:
                    residual += 1          # first call of a response should always have been dispatched
            events.append(CallEvent(it, pos, call["tool"], False, False, reason, is_lookup))
    unmatched_steps = [i for i, s in enumerate(steps)
                       if s.get("action") not in PSEUDO_ACTIONS and i not in consumed]
    residual += len(unmatched_steps)       # executed steps no model response accounts for

    proposed_n = len(events)
    discarded = [e for e in events if not e.executed]
    reasons = {}
    for e in discarded:
        reasons[e.discard_reason] = reasons.get(e.discard_reason, 0) + 1

    use_events = []
    for it, msg in enumerate(assistant, start=1):
        calls, ok = _proposed_calls(msg.get("content"))
        for pos, call in enumerate(calls if ok else []):
            ev = next((e for e in events if e.iteration == it and e.position == pos), None)
            for u in _identifier_uses(call["tool"], call["arguments"]):
                use_events.append((u, bool(ev and ev.executed)))
    proposed_only = {u for u, ex in use_events if not ex} - {u for u, ex in use_events if ex}

    if adapter_capture_complete is True:
        emitted = verified(proposed_n)
    elif adapter_capture_complete is False:
        emitted = Observed(proposed_n, INDETERMINATE,
                           "adapter capture is known to be incomplete; this is a lower bound")
    else:
        emitted = Observed(proposed_n, INDETERMINATE,
                           "whether the adapter returns every call the model emitted is unverified; "
                           "this is the number the harness received (a lower bound)")

    if residual:
        identity = Observed(
            {"holds": False, "residual": residual}, INDETERMINATE,
            "proposed calls and recorded steps do not reconcile; a recording or analysis defect, "
            "never attributed to the agent")
    elif adapter_capture_complete is True:
        identity = verified({"holds": True, "residual": 0})
    else:
        identity = Observed({"holds": None, "residual": 0}, INDETERMINATE,
                            "proposed = executed + discarded holds for what the harness received, "
                            "but emitted = executed + discarded cannot be asserted without complete "
                            "adapter capture")

    return TrialProvenance(
        **common, exec_mode=mode, history_format="minilab",
        model_response_iterations=verified(len(assistant)),
        model_requests_failed=verified(failed_requests),
        calls_proposed=verified(proposed_n), calls_emitted=emitted,
        calls_executed=verified(executed_n),
        calls_discarded=verified(len(discarded)), calls_failed=verified(failed_n),
        lookup_calls_proposed=verified(sum(1 for e in events if e.is_lookup)),
        lookup_calls_executed=verified(len(lookups_exec)),
        lookup_calls_discarded=verified(sum(1 for e in discarded if e.is_lookup)),
        lookup_calls_failed=verified(sum(1 for s in lookups_exec if _is_error(s.get("observation")))),
        lookup_empty_results=verified(empty_n), retries=retries,
        distinct_identifiers_attempted=verified(len(attempted)),
        distinct_identifiers_observed=verified(len(set(emissions))),
        distinct_identifiers_proposed_only_in_discarded_calls=verified(len(proposed_only)),
        identifier_emission_events=verified(len(emissions)),
        identifier_use_events=verified(len(use_events)),
        identifier_use_events_executed=verified(sum(1 for _, ex in use_events if ex)),
        accounting_identity=identity, discard_reasons=reasons, events=tuple(events))


# ---------------------------------------------------------------------------
# Budget plug-in point (no policy is chosen here; owner decision N1/U15).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BudgetAssessment:
    policy: str
    status: str            # "no_policy_configured" | "within" | "exceeded" | "indeterminate"
    detail: str = ""


class BudgetPolicy:
    """Interface: assess(TrialProvenance) -> BudgetAssessment. Analysis only;
    a policy never stops a run from here."""

    name = "abstract"

    def assess(self, provenance: TrialProvenance) -> BudgetAssessment:  # pragma: no cover
        raise NotImplementedError


class NoBudgetPolicy(BudgetPolicy):
    name = "none"

    def assess(self, provenance):
        return BudgetAssessment(self.name, "no_policy_configured",
                                "no execution budget rule has been approved (N1, U15 open)")


class IterationCapPolicy(BudgetPolicy):
    """Example policy for tests and future use: a cap on model-response
    iterations. NOT the approved EXP-005 rule."""

    name = "iteration_cap"

    def __init__(self, cap: int):
        if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1:
            raise ValueError("cap must be a positive int")
        self.cap = cap

    def assess(self, provenance):
        it = provenance.model_response_iterations
        if it.status != VERIFIED:
            return BudgetAssessment(self.name, "indeterminate", it.reason)
        used = it.value + (provenance.model_requests_failed.value or 0)
        return BudgetAssessment(self.name, "within" if used <= self.cap else "exceeded",
                                f"{used} iteration(s) against a cap of {self.cap}")


def analyze_jsonl(path, **kwargs):
    """analyze_trial over every line of a trajectory JSONL file (read-only)."""
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(analyze_trial(json.loads(line), **kwargs))
    return out
