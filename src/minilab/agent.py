"""Agent loop: plain ReAct. No framework, no abstractions.

Hardening contract (E1): this function never raises for model or tool
behavior. Model exceptions -> model_error termination. Any dispatch
exception -> tool_error termination (error-dict observations are NOT
terminal; they are recorded and the loop continues). Every call returns
a Trajectory, so the runner always has a row to write.
"""

import hashlib
import json

from .tools import TOOL_NAMES, TOOL_SCHEMAS, build_system_prompt, dispatch
from .trajectory import Step, Trajectory


def _fault(boundary, exc):
    """Typed record of WHERE an exception was caught and WHAT it was, kept
    beside the error text. `boundary` is the code location, which is known
    exactly; whose fault it was is a separate question (see
    failures.fault_origin). No message text is used for this."""
    cls = type(exc)
    bases = [b.__name__ for b in cls.__mro__[1:]
             if b.__name__ not in ("object", "BaseException", "Exception")][:6]
    record = {"boundary": boundary, "exception_type": cls.__name__,
              "exception_module": cls.__module__ or "", "exception_bases": bases}
    # HTTP status, when the exception object itself carries one (SDK
    # response errors do). Read from the object, never parsed from text.
    for attr in ("status_code", "code"):
        status = getattr(exc, attr, None)
        if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
            record["http_status"] = status
            break
    return record


_JSON_TYPES = {"string": str, "integer": int, "number": (int, float), "array": list}


def _arguments_conform(name, args):
    """Do the arguments satisfy the tool's DECLARED schema (required params
    present, declared types)? True / False, or None when the tool is unknown.
    Used only to annotate a fault record; dispatch itself is unchanged. A
    schema can be incomplete, so True does not prove the arguments were
    sensible, only that they met what the agent was told."""
    schema = next((t for t in TOOL_SCHEMAS if t["name"] == name), None)
    if schema is None or not isinstance(args, dict):
        return None
    for param, spec in schema["params"].items():
        if param not in args or args[param] is None:
            if spec.get("required"):
                return False
            continue
        value, expected = args[param], _JSON_TYPES.get(spec.get("type"))
        if expected is None:
            continue
        if isinstance(value, bool) or not isinstance(value, expected):
            return False
        if spec.get("type") == "array" and spec.get("items", {}).get("type") == "number":
            if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
                return False
    return True


def _tool_fault(name, args, exc):
    record = _fault("tool_dispatch", exc)
    record["tool_known"] = name in TOOL_NAMES
    record["arguments_schema_valid"] = _arguments_conform(name, args)
    return record


def _prompt_hash(prompt):
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _model_config(meta):
    meta = meta or {}
    return {
        "model": meta.get("model", ""),
        "provider": meta.get("provider", ""),
        "model_version": meta.get("model_version"),
        "temperature": meta.get("temperature", 0.0),
        "seed": meta.get("seed", 0),
        "max_steps": meta.get("max_steps", 8),
        "task_id": meta.get("task_id", ""),
        "experiment_id": meta.get("experiment_id", ""),
    }


def _as_call_list(out):
    """Normalize one model response to a non-empty call list, else None.

    Accepts {"tool", "arguments"} (single call, historical shape) or
    {"calls": [...]} (multi-call emission). Anything else — including an
    empty calls list — is malformed and yields None.
    """
    if isinstance(out, dict) and isinstance(out.get("calls"), list) and out["calls"]:
        calls = out["calls"]
    elif (isinstance(out, dict) and "tool" in out
            and isinstance(out.get("arguments", {}), dict)):
        calls = [{"tool": out["tool"], "arguments": out.get("arguments", {}) or {}}]
    else:
        return None
    normalized = []
    for c in calls:
        if (not isinstance(c, dict) or "tool" not in c
                or not isinstance(c.get("arguments", {}), dict)):
            return None
        normalized.append({"tool": c["tool"], "arguments": c.get("arguments", {}) or {}})
    return normalized


def run_agent(task_id, task_prompt, env, model_fn, max_steps=8, meta=None,
              exec_mode="sequential"):
    """Run one episode. Always returns a Trajectory, never raises.

    model_fn(messages, tool_schemas, config) -> {"tool", "arguments"} | {"calls", [...]} | {"final"}.
    messages is a plain list of {"role", "content"} dicts; observations are
    JSON-serialized. config carries model/experiment settings explicitly.
    meta carries trajectory provenance (experiment_id, run_id, timestamp,
    model, provider, temperature, seed); max_steps/task_id/prompt come from
    the explicit arguments.
    exec_mode selects batch handling: "sequential" executes only the first
    emitted call per turn (historical behavior, default); "batch" executes
    every emitted call in listed order through the same synchronous
    dispatch. Either way one loop iteration — one model-call budget unit —
    covers one model response. Unknown exec_mode raises ValueError.
    """
    if exec_mode not in ("sequential", "batch"):
        raise ValueError(
            f"unknown exec_mode: {exec_mode!r} (use 'sequential' or 'batch').")
    meta = meta or {}
    messages = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": task_prompt},
    ]
    raw_history = [dict(m) for m in messages]
    config = _model_config({**meta, "max_steps": max_steps, "task_id": task_id})
    steps = []
    final_answer = None
    termination_reason = "max_steps"

    for i in range(1, max_steps + 1):
        try:
            out = model_fn(list(messages), TOOL_SCHEMAS, config)
        except Exception as e:  # noqa: BLE001 - model/API faults become data
            steps.append(Step(step=i, action="model_error", arguments={},
                              observation={"error": f"model error: {e!r}",
                                           "fault": _fault("model_call", e)}))
            termination_reason = "model_error"
            break
        raw_history.append({"role": "assistant", "content": out})

        if isinstance(out, dict) and "final" in out:
            final_answer = out["final"]
            termination_reason = "agent_final"
            break

        calls = _as_call_list(out)
        if calls is None:
            steps.append(Step(step=i, action="invalid_action", arguments={},
                              observation={"error": f"invalid model output: {out!r}",
                                           "fault": {"boundary": "model_output_validation"}}))
            termination_reason = "invalid_action"
            break

        if exec_mode == "sequential":
            calls = calls[:1]
        turn_failed = False
        for call in calls:
            name, args = call["tool"], call.get("arguments", {}) or {}
            try:
                obs = dispatch(env, name, args)
            except ValueError as e:
                steps.append(Step(step=i, action=name, arguments=args,
                                  observation={"error": str(e), "fault": _tool_fault(name, args, e)}))
                termination_reason = "tool_error"
                turn_failed = True
                break
            except Exception as e:  # noqa: BLE001 - tool/infra faults terminate, never crash
                steps.append(Step(step=i, action=name, arguments=args,
                                  observation={"error": f"tool error: {e!r}",
                                               "fault": _tool_fault(name, args, e)}))
                termination_reason = "tool_error"
                turn_failed = True
                break
            steps.append(Step(step=i, action=name, arguments=args, observation=obs))
            obs_json = json.dumps(obs, default=str)
            messages.append({"role": "assistant", "content": {"tool": name, "arguments": args}})
            messages.append({"role": "user", "content": f"observation: {obs_json}"})
            raw_history.append({"role": "user", "content": f"observation: {obs_json}"})
        if turn_failed:
            break

    return Trajectory(
        task_id=task_id,
        steps=steps,
        final_answer=final_answer,
        termination_reason=termination_reason,
        raw_history=raw_history,
        experiment_id=meta.get("experiment_id", ""),
        run_id=meta.get("run_id", ""),
        timestamp=meta.get("timestamp", ""),
        model=meta.get("model", ""),
        provider=meta.get("provider", ""),
        model_version=meta.get("model_version"),
        temperature=meta.get("temperature", 0.0),
        seed=meta.get("seed", 0),
        max_steps=max_steps,
        prompt=task_prompt,
        prompt_hash=_prompt_hash(task_prompt),
    )
