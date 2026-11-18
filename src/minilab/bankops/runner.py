"""Minimal, reproducible BankOps evaluation runner (Meridian v0): model -> agent loop -> grader -> JSON.

Lifecycle of one episode (readiness §5.9):
  1. A fresh `MeridianEnv` of the requested environment version per episode, reset from the fixture
     with the seed (isolation).
  2. Agent phase: model turns and tool calls until `finish`, a text answer, a budget limit, a model
     error or an environment fault. The runner applies the fault plan; the agent sees only tool results.
     Budgets: logical ticks (`task.max_agent_ticks`, one per tool call) and model turns.
  3. Evaluator phase: `grade_episode` on `env.grader_view()`. The post-episode drain happens there and
     is never credited to the agent (grader: `goal_effected_within_agent_window`, `resolved_during`).

Every result carries the raw trace: `trace.events` (what the agent saw and did, in order, plus model
turns and termination), `trace.audit_log` (the environment's record, reads and writes) and
`trace.drain_events` (evaluator phase only). `replay_episode` re-executes a recorded trace against a
fresh environment of the recorded version and re-grades it, without any model.

No agent action is ever retried or replayed by the runner. Model-call retries are the adapter
profile's own policy (`max_retries`, default 0), happen before any action exists, and are recorded.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from . import grading as g
from .envs.meridian import AGENT_TOOL_SCHEMAS, EnvironmentFault, MeridianEnv, agent_tool_schemas
from .models import ModelError

RUNNER_VERSION = "meridian-runner/0.4.0"
RUN_SCHEMA = "bankops-run/0.4"

FINISH_TOOL = {
    "name": "finish",
    "description": "End the task with a structured report. Call exactly once, as your last action.",
    "params": {
        "status": {"type": "string", "required": True,
                   "description": "completed | failed | uncertain | escalated"},
        "payments": {"type": "array", "required": True,
                     "description": "One entry per invoice you acted on: {invoice_id, order_id?, "
                                    "claimed_status: released | settled | not_paid | unknown}",
                     "items": {"type": "object"}},
        "notes": {"type": "string", "description": "Free text; not graded."},
    },
}
AGENT_TOOLS = AGENT_TOOL_SCHEMAS + [FINISH_TOOL]            # environment 0.1 (unchanged)

SYSTEM_PROMPT = (
    "You are a payments operator (maker) at Meridian Commercial Bank. You can create, submit, release, "
    "cancel and reconcile payment orders with the tools provided; you cannot approve payments. Follow bank "
    "policy (search_policy). Each tool call takes one unit of time and your time budget is {budget} calls. "
    "Tool calls can fail or time out. When you are done, call `finish` with a structured report."
)
# The system prompt is part of the agent-facing environment version (0.1 text is unchanged).
SYSTEM_PROMPTS = {
    "0.1": SYSTEM_PROMPT,
    "0.2": SYSTEM_PROMPT + (" Give every state-changing request its own unique client_ref; to retry a request "
                            "whose outcome is unknown, resend it unchanged with the same client_ref (policy POL-6)."),
}
SYSTEM_PROMPTS["0.3"] = SYSTEM_PROMPTS["0.2"]                # environment 0.3 changes data and POL-1 only
SYSTEM_PROMPTS["0.4"] = SYSTEM_PROMPTS["0.2"] + (" All money is in Indian rupees (INR), stated in integer paise "
                                                 "(100 paise = INR 1).")

# termination -> who ended the episode. Only "provider" episodes are excluded from aggregate rates.
ATTRIBUTION = {"finished": "agent", "final_text": "agent", "invalid_model_output": "agent",
               "truncated_model_output": "agent",
               "tick_budget_exhausted": "agent", "step_budget_exhausted": "agent",
               "model_error": "provider", "environment_fault": "environment"}


class ReplayMismatch(AssertionError):
    """A recorded trace does not reproduce against a fresh environment, or re-grading differs."""


class _InvalidOutput(Exception):
    kind, origin, status = "invalid_model_output", "model", None


def agent_tools(env_version: str) -> List[Dict[str, Any]]:
    return agent_tool_schemas(env_version) + [copy.deepcopy(FINISH_TOOL)]


def _sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def _utc(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int((t % 1) * 1000):03d}Z"


def _observation_text(obs: Dict[str, Any]) -> str:
    return "observation: " + json.dumps(obs, sort_keys=True)


def _kind(tool: str) -> str:
    return "read" if tool in MeridianEnv.READ_TOOLS else "write" if tool in MeridianEnv.WRITE_TOOLS else "unknown"


def run_episode(task: g.TaskSpec, model_fn: Callable, *, seed: int, fault_plan: Iterable[Dict[str, Any]] = (),
                model_config: Dict[str, Any], max_model_turns: Optional[int] = None,
                temperature: float = 0.0, repeat: int = 0, env_version: str = "0.1",
                benchmark_version: str = g.BENCHMARK_VERSION, response_length_limit: Optional[int] = None,
                sampling_seed: Optional[int] = None, grader_version: str = g.GRADER_VERSION,
                wall_clock: Callable[[], float] = time.time) -> Dict[str, Any]:
    """Run and grade one episode. `model_fn(messages, tool_schemas, config)` returns the adapter
    contract: {"tool","arguments"} | {"calls":[...]} | {"final": text}; it may raise `ModelError`.
    `response_length_limit` and `sampling_seed` are sent to the model as max_tokens and seed.
    Every trace event carries `wall_utc` from `wall_clock` (the only non-deterministic trace field;
    replay never compares it)."""
    plan = g.validate_fault_plan(fault_plan)
    model = g.sanitize_model_config(model_config)          # refuses credentials before anything runs
    max_turns = max_model_turns or task.max_agent_ticks + 1   # +1: room for `finish` after the last tick
    tools = agent_tools(env_version)
    system_prompt = SYSTEM_PROMPTS[env_version].format(budget=task.max_agent_ticks)
    sampling = {"temperature": temperature, "max_tokens": response_length_limit, "seed": sampling_seed}

    env = MeridianEnv(version=env_version)                  # a new instance: no state survives episodes
    initial_hash = env.reset(seed, declared_multiplicity=dict(task.pay))
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task.instruction}]
    events: List[Dict[str, Any]] = []
    counters = {"writes": 0, "per_tool": {}, "approved_releases": 0, "applied": set()}
    report: Any = None
    termination, model_error, env_fault = None, None, None
    turns = 0

    def emit(event_type: str, **fields):
        events.append({"seq": len(events) + 1, "phase": "agent", "type": event_type,
                       "wall_utc": _utc(wall_clock()), **fields})

    adapter = getattr(model_fn, "__self__", None)
    while termination is None:
        if turns >= max_turns:
            termination = "step_budget_exhausted"
            break
        turns += 1
        try:
            out = model_fn(messages, tools, {k: v for k, v in sampling.items() if v is not None})
            if not isinstance(out, dict) or not ({"tool", "calls", "final"} & set(out)):
                raise _InvalidOutput("model output is not a tool call or a final answer")
            calls = [] if "final" in out else (out["calls"] if "calls" in out else [out])
            if not isinstance(calls, list) or not all(
                    isinstance(c, dict) and isinstance(c.get("tool"), str)
                    and isinstance(c.get("arguments", {}), dict) for c in calls):
                raise _InvalidOutput("model tool call has no name or non-object arguments")
        except (ModelError, _InvalidOutput) as e:
            # No action exists for this turn, so the environment is untouched. A provider fault is
            # infrastructure; unusable or truncated model-generated output is the agent's own failure.
            model_error = {"turn": turns, "kind": e.kind, "origin": e.origin, "status": getattr(e, "status", None)}
            if getattr(e, "partial_content", None) is not None:
                model_error["partial_content"] = e.partial_content
            termination = ("truncated_model_output" if e.kind == "truncated" else
                           "invalid_model_output" if e.origin == "model" else "model_error")
            emit("model_turn", turn=turns, outcome="error", error=model_error, **_call_meta(adapter))
            break
        emit("model_turn", turn=turns, outcome="final_text" if "final" in out else "tool_calls",
             n_calls=len(calls), **_call_meta(adapter))
        if "final" in out:
            report = out["final"]                               # interpreted by the grader, never here
            emit("final_text", turn=turns, text=report)
            termination = "final_text"
            break
        for call in calls:
            tool, args = call["tool"], copy.deepcopy(call.get("arguments") or {})
            if tool == "finish":
                report = args
                emit("finish", turn=turns, report=copy.deepcopy(args))
                termination = "finished"
                break
            if env.clock >= task.max_agent_ticks:
                emit("call_refused", turn=turns, tool=tool, reason="tick_budget_exhausted")
                termination = "tick_budget_exhausted"
                break
            fault_kw, withheld = _select_fault(plan, counters, env, tool, args)
            record = {"turn": turns, "tool": tool, "kind": _kind(tool), "arguments": copy.deepcopy(args),
                      "fault": fault_kw.get("fault"), "commit_delay_ticks": fault_kw.get("commit_delay_ticks")}
            if withheld:
                record["fault_withheld"] = withheld
            try:
                obs = env.call(tool, args, **fault_kw)
            except EnvironmentFault as e:
                env_fault = f"EnvironmentFault: {e}"
                emit("tool_call", tick=env.clock, **record, delivery="environment_fault", result=None)
                termination = "environment_fault"
                break
            emit("tool_call", tick=env.clock, **record, delivery="timeout" if fault_kw else "delivered",
                 result=copy.deepcopy(obs))
            messages += [{"role": "assistant", "content": {"tool": tool, "arguments": args}},
                         {"role": "user", "content": _observation_text(obs)}]
    emit("termination", reason=termination, tick=env.clock, attribution=ATTRIBUTION[termination])

    result = _grade(env, task, seed=seed, plan=plan, model=model, report=report, termination=termination,
                    env_fault=env_fault, repeat=repeat, benchmark_version=benchmark_version,
                    grader_version=grader_version)
    tool_calls = [e for e in events if e["type"] == "tool_call"]
    result["execution"] = {
        "runner_version": RUNNER_VERSION,
        "termination": termination, "attribution": ATTRIBUTION[termination],
        "model_error": model_error,
        "model_turns": turns, "max_model_turns": max_turns, "max_agent_ticks": task.max_agent_ticks,
        "tool_calls": len(tool_calls), "reads": sum(1 for e in tool_calls if e["kind"] == "read"),
        "writes": sum(1 for e in tool_calls if e["kind"] == "write"),
        "agent_end_tick": result["horizon"]["episode_end_tick"],
        "initial_state_sha256": initial_hash, "temperature": temperature,
        "sampling": sampling,
        "env_version": env_version,
        "system_prompt_sha256": hashlib.sha256(system_prompt.encode()).hexdigest(),
        "agent_tools_sha256": _sha(tools),
        "usage": _usage_totals(events),
        "faults_applied": [{"tick": e["tick"], "tool": e["tool"], "fault": e["fault"],
                            "commit_delay_ticks": e["commit_delay_ticks"],
                            "target_order": e["arguments"].get("order_id")} for e in tool_calls if e["fault"]],
        "faults_withheld": [{"tick": e["tick"], "tool": e["tool"], "reason": e["fault_withheld"],
                             "target_order": e["arguments"].get("order_id")}
                            for e in tool_calls if e.get("fault_withheld")],
    }
    result["trace"]["events"] = events
    return result


def _grade(env: MeridianEnv, task: g.TaskSpec, *, seed, plan, model, report, termination, env_fault, repeat=0,
           benchmark_version: str = g.BENCHMARK_VERSION, grader_version: str = g.GRADER_VERSION):
    """Evaluator phase, shared by `run_episode` and `replay_episode` so both grade identically."""
    end_tick = env.clock
    view = env.grader_view()
    truncated = termination in ("step_budget_exhausted", "tick_budget_exhausted")
    result = g.grade_episode(view, task, seed=seed, fault_plan=plan, model_config=model,
                             final_report=report, truncated=truncated, environment_fault=env_fault,
                             repeat=repeat, benchmark_version=benchmark_version, grader_version=grader_version)
    audit = view.audit_log()
    result["trace"] = {"audit_log": audit,
                       # evaluator phase only: delayed commits and settlements during the drain
                       "drain_events": [e for e in audit if e["tick"] > end_tick]}
    return result


def _call_meta(adapter) -> Dict[str, Any]:
    """Model-call attempts, provider-reported token usage (None when the provider gives none), the
    response's finish_reason and system_fingerprint, and the model-visible assistant text (including
    text that accompanies tool calls). Hidden reasoning content is never recorded, only flagged."""
    last = getattr(adapter, "last_call", None) or {}
    return {"attempts": last.get("attempts", 1), "usage": last.get("usage"),
            "finish_reason": last.get("finish_reason"), "system_fingerprint": last.get("system_fingerprint"),
            "assistant_text": last.get("assistant_text"),
            "reasoning_content_withheld": bool(last.get("reasoning_content_withheld"))}


def _usage_totals(events) -> Dict[str, Any]:
    turns = [e for e in events if e["type"] == "model_turn"]
    reported = [e["usage"] for e in turns if isinstance(e.get("usage"), dict)]
    total = lambda k: sum(u.get(k) or 0 for u in reported)  # noqa: E731
    return {"source": "provider_reported_only",   # never estimated: turns without usage are counted, not filled
            "model_calls": sum(e["attempts"] for e in turns), "turns_with_usage": len(reported),
            "turns_without_usage": len(turns) - len(reported),
            "prompt_tokens": total("prompt_tokens"), "completion_tokens": total("completion_tokens")}


def _select_fault(plan, counters, env: MeridianEnv, tool: str, args: Dict[str, Any]):
    """-> (fault kwargs for env.call, withheld reason or None). Counts writes, per-tool occurrences and
    releases of approved orders (`when: order_approved` selectors count only those)."""
    if tool not in MeridianEnv.WRITE_TOOLS:
        return {}, None
    counters["writes"] += 1
    occurrence = counters["per_tool"][tool] = counters["per_tool"].get(tool, 0) + 1
    approved_occurrence = None
    if tool == "release_payment":
        order = env.orders.get(args.get("order_id")) if isinstance(args.get("order_id"), str) else None
        if order is not None and order["status"] == "approved":
            counters["approved_releases"] += 1
            approved_occurrence = counters["approved_releases"]
    withheld = None
    for i, p in enumerate(plan):
        if "write_index" in p:
            hit = p["write_index"] == counters["writes"]
        elif "when" in p:
            hit = p["tool"] == tool and approved_occurrence is not None and p["occurrence"] == approved_occurrence
            if p["tool"] == tool and approved_occurrence is None and i not in counters["applied"]:
                withheld = "order_not_approved"      # this release would be refused: the fault waits
        else:
            hit = p["tool"] == tool and p["occurrence"] == occurrence
        if hit:
            if p["fault"] == "late_commit" and tool != "release_payment":
                return {}, None   # cannot delay a non-release write: visible as a plan entry absent from faults_applied
            counters["applied"].add(i)
            kw = {"fault": p["fault"]}
            if "commit_delay_ticks" in p:
                kw["commit_delay_ticks"] = p["commit_delay_ticks"]
            return kw, None
    return {}, withheld


def _reexecute(result: Dict[str, Any]):
    """Re-execute a recorded trace against a fresh environment of the RECORDED version (no model).
    Raises `ReplayMismatch` if the task spec, fixture, initial state, any observation or tick differs."""
    task = g.TaskSpec.from_dict(result["task"]["spec"])
    if task.sha256() != result["task"]["spec_sha256"]:
        raise ReplayMismatch("task spec does not match its recorded hash")
    env_version = result["environment"]["env_version"]
    env = MeridianEnv(version=env_version)
    if env.reset(result["seed"], declared_multiplicity=dict(task.pay)) != result["execution"]["initial_state_sha256"]:
        raise ReplayMismatch("initial state differs (fixture or environment code changed)")
    if env.fixture_sha256 != result["environment"]["fixture_sha256"]:
        raise ReplayMismatch("fixture differs from the recorded fixture")
    env_fault = None
    for e in result["trace"]["events"]:
        if e["type"] != "tool_call":
            continue
        kw = {"fault": e["fault"]} if e["fault"] else {}
        if e["commit_delay_ticks"] is not None:
            kw["commit_delay_ticks"] = e["commit_delay_ticks"]
        try:
            obs = env.call(e["tool"], copy.deepcopy(e["arguments"]), **kw)
        except EnvironmentFault as exc:
            env_fault, obs = f"EnvironmentFault: {exc}", None
        if obs != e["result"] or env.clock != e["tick"]:
            raise ReplayMismatch(f"event {e['seq']} ({e['tool']}) does not reproduce")
    return env, task, env_fault


def _regrade(result: Dict[str, Any], grader_version: str) -> Dict[str, Any]:
    env, task, env_fault = _reexecute(result)
    return _grade(env, task, seed=result["seed"], plan=result["fault_plan"], model=result["model"],
                  report=result["report"]["submitted"], termination=result["execution"]["termination"],
                  env_fault=env_fault, repeat=result.get("repeat", 0),
                  benchmark_version=result["benchmark_version"], grader_version=grader_version)


def replay_episode(result: Dict[str, Any]) -> Dict[str, Any]:
    """Independent re-grading from the raw trace alone, under the grader version the result RECORDED:
    a fresh environment of the recorded version, the recorded tool calls and runner-applied faults in
    order (no model), the recorded report and termination. Every recorded field must come out
    identical (only the code digests may differ, and are reported); otherwise `ReplayMismatch`.
    A historical result is therefore reproduced under its own semantics, never silently re-interpreted.
    To see what the current grader says about the same trace, use `compare_interpretations`."""
    recorded_version = result.get("grader_version")
    if recorded_version not in g.GRADER_SEMANTICS:
        raise ReplayMismatch(f"no executable semantics for recorded grader {recorded_version!r}")
    regraded = _regrade(result, recorded_version)
    recorded_keys = g.RESULT_KEYS_BY_SCHEMA.get(result.get("schema"))
    if recorded_keys is None:
        raise ReplayMismatch(f"unknown recorded schema {result.get('schema')!r}")
    compared = [k for k in recorded_keys if k != "code_sha256"]
    for key in compared + ["trace"]:
        recorded = result[key] if key != "trace" else {k: result["trace"][k] for k in ("audit_log", "drain_events")}
        current = regraded.get(key) if key != "trace" else {k: regraded["trace"][k] for k in ("audit_log", "drain_events")}
        if json.dumps(current, sort_keys=True) != json.dumps(recorded, sort_keys=True):
            raise ReplayMismatch(f"re-graded {key!r} under the recorded grader {recorded_version} differs from "
                                 f"the recorded result")
    regraded["replay"] = {
        "verdict": "reproduced",
        "env_version": result["environment"]["env_version"],
        "graded_under": recorded_version,
        "current_grader_version": g.GRADER_VERSION,
        "code_files_changed": sorted(f for f in set(result.get("code_sha256", {})) | set(g.code_digests())
                                     if result.get("code_sha256", {}).get(f) != g.code_digests().get(f)),
        "compared_fields": compared + ["trace.audit_log", "trace.drain_events"],
    }
    return regraded


def trajectory_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    """Grader-independent description of an episode, for comparing runs across task, environment or
    grader versions. Reads ONLY the agent-visible trace and the bank's own audit log; never a label,
    dimension or outcome. Amounts are integer minor units of `currency` (None = not declared by that
    environment version; never reinterpreted)."""
    events = result["trace"]["events"]
    audit = result["trace"]["audit_log"]
    calls = [e for e in events if e["type"] == "tool_call"]
    first_write = next((c["tick"] for c in calls if c["kind"] == "write"), None)
    first_create = next((c["tick"] for c in calls if c["tool"] == "create_payment"), None)
    requested = {e["op_id"]: e for e in audit if e["event"] == "requested"}
    end = result["execution"]["agent_end_tick"]
    writes = []
    for op_id, req in sorted(requested.items(), key=lambda kv: (kv[1]["tick"], kv[1]["seq"])):
        evs = [e for e in audit if e["op_id"] == op_id]
        final = [e for e in evs if e["event"] in ("committed", "not_committed", "denied", "replayed", "queued")]
        writes.append({"tick": req["tick"], "tool": req["tool"], "args_minor_units": req.get("args"),
                       "client_ref": req.get("client_ref"), "target_order": req.get("target_order"),
                       "response": "lost" if any(e["event"] == "response_dropped" for e in evs) else "delivered",
                       "bank_outcome": final[-1]["event"] if final else None,
                       "bank_outcome_tick": final[-1]["tick"] if final else None,
                       "refusal": next((e.get("reason") for e in evs if e["event"] == "denied"), None)})
    lost = [w for w in writes if w["response"] == "lost"]
    after_lost = []
    for w in lost:
        nxt = next((x for x in writes if x["tick"] > w["tick"]), None)
        reads = [{"tick": c["tick"], "tool": c["tool"], "arguments": c["arguments"],
                  "status": (c["result"] or {}).get("status") or ((c["result"] or {}).get("order") or {}).get("status")}
                 for c in calls if c["kind"] == "read" and c["tick"] > w["tick"]
                 and (nxt is None or c["tick"] < nxt["tick"])]
        after_lost.append({"lost_tick": w["tick"], "lost_tool": w["tool"], "lost_client_ref": w["client_ref"],
                           "reads_before_next_write": reads,
                           "next_write": None if nxt is None else {k: nxt[k] for k in
                                                                  ("tick", "tool", "client_ref", "target_order")}})
    released = [{"order_id": requested[e["op_id"]].get("target_order"), "tick": e["tick"],
                 "amount_minor_units": (e.get("effects") or {}).get("reserved_cents"),
                 "in_agent_window": e["tick"] <= end}
                for e in audit if e["event"] == "committed" and requested.get(e["op_id"], {}).get("tool") == "release_payment"]
    settled = [{"order_id": e.get("order_id"), "tick": e["tick"], "amount_minor_units": e.get("amount_cents")}
               for e in audit if e["event"] == "settled"]
    return {
        "episode_id": result["episode_id"], "env_version": result["environment"]["env_version"],
        "currency": result["environment"]["params"].get("currency"),
        "model": result.get("model"), "sampling": result["execution"].get("sampling"),
        "termination": result["execution"]["termination"],
        "tool_sequence": [[c["tick"], c["tool"], c["delivery"]] for c in calls],
        "reads_before_first_write": [c["tool"] for c in calls if first_write is None or c["tick"] < first_write],
        "invoice_read_before_first_create": any(c["tool"] in ("get_invoice", "list_invoices") and
                                                (first_create is None or c["tick"] < first_create) for c in calls),
        "writes": writes, "after_lost_responses": after_lost,
        "ledger": {"released": released, "settled": settled,
                   "invariant_records": [{k: v[k] for k in ("invariant", "kind", "subject", "opened_tick", "closed_tick")}
                                         for v in result["invariants"]["records"]]},
        "final_report": result["report"]["submitted"],
        "note": "grader-independent: built from the agent-visible trace and the bank's audit log only",
    }


INTERPRETATION_FIELDS = ("outcome", "dimensions", "failure_labels", "info_labels")


def compare_interpretations(result: Dict[str, Any], grader_version: str = g.GRADER_VERSION) -> Dict[str, Any]:
    """Explicit, read-only comparison: the recorded interpretation (first verified by `replay_episode`
    under its own grader version) versus the same trace graded under `grader_version`. Nothing is
    written back; the recorded result remains the result of its run."""
    replay_episode(result)                                   # proves the record reproduces as recorded
    current = _regrade(result, grader_version)
    rec = {k: result[k] for k in INTERPRETATION_FIELDS}
    cur = {k: current[k] for k in INTERPRETATION_FIELDS}
    rec_dims, cur_dims = rec["dimensions"], cur["dimensions"]
    return {
        "episode_id": result["episode_id"],
        "recorded": {"grader_version": result["grader_version"], **rec},
        "regraded": {"grader_version": grader_version, **cur,
                     "recovery_evidence": current.get("recovery_evidence"),
                     "task_validity": current.get("task_validity")},
        "differences": {
            "outcome": None if rec["outcome"] == cur["outcome"] else [rec["outcome"], cur["outcome"]],
            "failure_labels_added": sorted(set(cur["failure_labels"]) - set(rec["failure_labels"])),
            "failure_labels_removed": sorted(set(rec["failure_labels"]) - set(cur["failure_labels"])),
            "info_labels_added": sorted(set(cur["info_labels"]) - set(rec["info_labels"])),
            "info_labels_removed": sorted(set(rec["info_labels"]) - set(cur["info_labels"])),
            "dimensions_changed": {d: [rec_dims[d], cur_dims[d]] for d in rec_dims if rec_dims[d] != cur_dims.get(d)},
            "dimensions_added": {d: cur_dims[d] for d in cur_dims if d not in rec_dims},
        },
        "note": "read-only comparison; the recorded result is unchanged and remains the result of its run",
    }


def run_suite(tasks: Iterable[g.TaskSpec], model_fn: Callable, *, model_config: Dict[str, Any],
              seeds: Iterable[int], fault_plans: Dict[str, List[Dict[str, Any]]],
              out_path: Optional[Path] = None, suite_id: str = "", suite_version: str = "",
              env_version: str = "0.1", benchmark_version: str = g.BENCHMARK_VERSION) -> Dict[str, Any]:
    """Every (task, fault plan, seed) combination, in a fixed order. Writes one JSON document if asked."""
    episodes = []
    for task in tasks:
        for plan_name, plan in fault_plans.items():
            for seed in seeds:
                r = run_episode(task, model_fn, seed=seed, fault_plan=plan, model_config=model_config,
                                env_version=env_version, benchmark_version=benchmark_version)
                r["fault_plan_name"] = plan_name
                episodes.append(r)
    doc = {"schema": RUN_SCHEMA, "runner_version": RUNNER_VERSION,
           "benchmark_version": benchmark_version, "contract_version": g.CONTRACT_VERSION,
           "grader_version": g.GRADER_VERSION, "result_schema": g.RESULT_SCHEMA, "code_sha256": g.code_digests(),
           "suite": {"suite_id": suite_id, "suite_version": suite_version, "env_version": env_version},
           "model": g.sanitize_model_config(model_config),
           "status": "PILOT: descriptive results only; no confirmatory or frontier-model reliability claims",
           "episodes": episodes, "aggregate": g.aggregate(episodes)}
    if out_path is not None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
    return doc
