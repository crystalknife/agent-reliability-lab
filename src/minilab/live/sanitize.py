"""The sanitization boundary between raw execution evidence and anything the
live store, the API or the dashboard may hold.

Four layers, kept apart:
  1. Raw execution evidence: trajectories and result rows (JSONL on disk,
     and the objects the runner hands to an event sink). NEVER stored in the
     live database and NEVER served.
  2. Sanitized run metadata: what `safe_trial` / `safe_experiment` return.
     Built by ALLOWLIST: a field is copied only if it is named here.
  3. Aggregated analytics: counts and rates computed from layer 2.
  4. Publicly publishable material: NOT decided here. Layer 2 and 3 are for
     a local, private dashboard; publishing needs the owner's U9 decision.

What never crosses the boundary: prompts, model messages, tool arguments,
observations, final answers, raw provider errors, evaluator reason strings
(they quote evidence values and identifiers), file paths, exception text.
"""

from __future__ import annotations

import re

from ..failures import FAILURES, FAULT_ATTRIBUTIONS, FAULT_BASES, FAULT_ORIGINS, TRIAL_STATUSES
from ..provenance import Observed, analyze_trial
from ..tools import TOOL_NAMES
from ..trajectory import TERMINATION_REASONS

FORBIDDEN_KEYS = frozenset({
    "prompt", "raw_history", "arguments", "observation", "observations", "final_answer",
    "steps", "messages", "content", "provider_error", "reasons", "api_key", "authorization",
    "token", "password", "secret", "traceback", "exception_text", "path", "results_path",
    "trajectories_path", "data_path",
})
MAX_STRING = 240

_SECRETISH = re.compile(r"(sk-[A-Za-z0-9]{8,}|AIza[0-9A-Za-z_\-]{10,}|ghp_[A-Za-z0-9]{10,}|Bearer\s+\S+)")
_PATHISH = re.compile(r"([A-Za-z]:[\\/]|\\\\|/home/|/Users/|/root/|/sessions/|/mnt/|/tmp/|%[A-Za-z]+%|\$env:)")
_LABEL = re.compile(r"[^A-Za-z0-9 ._:/@+#()\-]")

# evaluator reason text -> category (the text after the colon is dropped)
REASON_CATEGORIES = (
    ("bad termination", "bad_termination"),
    ("missing final answer", "missing_final_answer"),
    ("verdict mismatch", "verdict_mismatch"),
    ("missing evidence", "missing_evidence"),
    ("missing required tool calls", "missing_required_tool_calls"),
    ("ungrounded numbers", "ungrounded_numbers"),
    ("evaluator raised", "evaluator_raised"),
)


class UnsafePayload(ValueError):
    """A payload headed for the live store contains something it must not."""


def label(value, max_len=80):
    """A short display label: unknown characters removed, length capped,
    path-like or secret-like text replaced. For model/provider names etc."""
    if value is None:
        return None
    s = str(value)
    if _SECRETISH.search(s) or _PATHISH.search(s):
        return "[redacted]"
    return _LABEL.sub("", s)[:max_len]


def reason_categories(reasons):
    """Evaluator reasons reduced to their categories. The reason strings
    themselves quote evidence items and numbers, so they stay behind."""
    out = []
    for r in reasons or []:
        text = str(r).lower()
        cat = next((c for prefix, c in REASON_CATEGORIES if text.startswith(prefix)), "other")
        if cat not in out:
            out.append(cat)
    return out


def _enum(value, allowed, default="unknown"):
    return value if value in allowed else default


def _num(value, kind=(int, float)):
    return value if isinstance(value, kind) and not isinstance(value, bool) else None


def safe_provenance(trajectory, exec_mode):
    """Counts and statuses only (see provenance.analyze_trial). The reason
    texts are constants written in provenance.py, not record content."""
    mode = exec_mode if exec_mode in ("sequential", "batch") else None
    try:
        p = analyze_trial(trajectory, exec_mode=mode)
    except Exception as e:  # noqa: BLE001 - instrumentation must never break a run
        return {"available": False, "reason": f"provenance analysis failed ({type(e).__name__})"}
    out = {"available": True, "history_format": p.history_format,
           "discard_reasons": dict(p.discard_reasons), "completeness": p.completeness()}
    for name, v in p.__dict__.items():
        if isinstance(v, Observed):
            value = v.value
            if isinstance(value, dict):          # accounting identity
                value = {"holds": value.get("holds"), "residual": value.get("residual")}
            elif not (value is None or isinstance(value, (int, bool)) or value in ("sequential", "batch")):
                value = None
            out[name] = {"value": value, "status": v.status, "reason": v.reason}
    return out


def tool_activity(trajectory):
    """Executed steps as (iteration, tool name, outcome). No arguments, no
    observations. Unknown tool names are reported as 'unknown_tool'."""
    d = trajectory if isinstance(trajectory, dict) else trajectory.to_dict()
    out = []
    for s in d.get("steps", []):
        action = s.get("action")
        if action in ("model_error", "invalid_action"):
            kind = action
        elif action in TOOL_NAMES:
            kind = action
        else:
            kind = "unknown_tool"
        obs = s.get("observation")
        outcome = "error" if isinstance(obs, dict) and "error" in obs else (
            "empty" if obs == [] else "ok")
        out.append({"iteration": _num(s.get("step"), int), "tool": kind, "outcome": outcome})
    return out


def safe_trial(row, trajectory, *, seq=None, started_at=None, finished_at=None):
    """Sanitized summary of one finished trial, from the runner's result row
    and trajectory."""
    out = {
        "run_id": label(row.get("run_id"), 64),
        "experiment_id": label(row.get("experiment_id"), 64),
        "task_id": label(row.get("task_id"), 80),
        "seq": _num(seq, int),
        "seed": _num(row.get("seed"), int),
        "difficulty": label(row.get("difficulty"), 20),
        "trial_status": _enum(row.get("trial_status", "valid"), TRIAL_STATUSES),
        "passed": bool(row.get("passed")) if row.get("trial_status", "valid") == "valid" else None,
        "failure": _enum(row.get("failure"), FAILURES),
        "termination_reason": _enum(row.get("termination_reason"), TERMINATION_REASONS),
        "reason_categories": reason_categories(row.get("reasons")),
        "provider_error_present": bool(row.get("provider_error")),
        "fault_origin": _enum(row.get("fault_origin"), FAULT_ORIGINS, "not_recorded"),
        "fault_attribution": _enum(row.get("fault_attribution"), FAULT_ATTRIBUTIONS, "none"),
        "fault_basis": _enum(row.get("fault_basis"), FAULT_BASES, "none"),
        "model_requests": _num(row.get("model_requests"), int),
        "tool_calls": _num(row.get("tool_calls"), int),
        "max_steps": _num(row.get("max_steps"), int),
        "exec_mode": _enum(row.get("exec_mode"), ("sequential", "batch", "native")),
        "started_at": _num(started_at),
        "finished_at": _num(finished_at),
        "tool_activity": tool_activity(trajectory),
        "provenance": safe_provenance(trajectory, row.get("exec_mode")),
    }
    assert_safe(out)
    return out


def safe_experiment(info):
    """Sanitized experiment configuration from the runner's start event."""
    task_ids = [label(t, 80) for t in (info.get("task_ids") or [])][:500]
    out = {
        "experiment_id": label(info.get("experiment_id"), 64),
        "kind": _enum(info.get("kind"), ("run", "synthetic"), "run"),
        "label": label(info.get("label"), 80),
        "model": label(info.get("model")),
        "provider": label(info.get("provider"), 40),
        "framework": _enum(info.get("framework"), ("minilab", "langchain")),
        "strategy": label(info.get("strategy"), 60),
        "exec_mode": _enum(info.get("exec_mode"), ("sequential", "batch", "native")),
        "temperature": _num(info.get("temperature")),
        "seed": _num(info.get("seed"), int),
        "repeats": _num(info.get("repeats"), int),
        "task_ids": task_ids,
        "total_trials": _num(info.get("total_trials"), int),
        "code_revision": label(_manifest_commit(info) or info.get("code_revision"), 64),
        "code_dirty": _manifest_dirty(info),
        "task_schema_version": label(info.get("task_schema_version"), 8),
        "protocol_ref": label(info.get("protocol_ref"), 80),
        "manifest": safe_manifest(info.get("manifest")),
        "manifest_persistence": _safe_persistence(info.get("manifest_persistence")),
    }
    assert_safe(out)
    return out


def _safe_persistence(p):
    """Whether the optional manifest FILE was written (distinct from whether
    a manifest could be built)."""
    if not isinstance(p, dict):
        return {"status": "not_recorded"}
    out = {"status": _enum(p.get("status"), ("written", "failed", "not_requested"), "not_recorded")}
    if out["status"] == "failed":
        out["error_type"] = label(p.get("error_type"), 60)
    return out


def _field(f, convert):
    """One manifest field {status, value, reason} with its value passed
    through `convert`; anything malformed becomes unavailable."""
    if not isinstance(f, dict) or f.get("status") not in ("available", "unavailable"):
        return {"status": "unavailable", "value": None, "reason": "not recorded"}
    if f["status"] == "unavailable":
        return {"status": "unavailable", "value": None, "reason": label(f.get("reason"), 120)}
    try:
        return {"status": "available", "value": convert(f.get("value"))}
    except Exception:  # noqa: BLE001
        return {"status": "unavailable", "value": None, "reason": "malformed value"}


def _hex(v, n=64):
    s = str(v)
    if not re.fullmatch(r"[0-9a-f]{%d}" % n, s):
        raise ValueError("not a hash")
    return s


def safe_manifest(m):
    """Run manifest reduced to hashes, counts, versions and short labels.
    Built field by field; unknown keys are dropped."""
    if not isinstance(m, dict):
        return {"status": "unavailable", "reason": "no run manifest was recorded"}
    if m.get("status") == "unavailable":
        return {"status": "unavailable", "reason": label(m.get("reason"), 120)}
    git = m.get("git") if isinstance(m.get("git"), dict) else {}
    adapter = m.get("adapter") if isinstance(m.get("adapter"), dict) else {}
    out = {
        "status": "available",
        "manifest_version": label(m.get("manifest_version"), 8),
        "manifest_sha256": _field({"status": "available", "value": m.get("manifest_sha256")}, _hex),
        "git_commit": _field(git.get("commit"), lambda v: _hex(v, 40)),
        "worktree": _field(git.get("worktree"), lambda v: {
            "state": _enum(v.get("state"), ("clean", "dirty")),
            "changed_tracked_files": _num(v.get("changed_tracked_files"), int),
            "untracked_files": _num(v.get("untracked_files"), int),
            "scope": [label(x, 40) for x in (v.get("scope") or [])][:10]}),
        "source": _field(m.get("source"), lambda v: {"files": _num(v.get("files"), int),
                                                     "sha256": _hex(v.get("sha256"))}),
        "source_ref": _field(m.get("source_ref"), lambda v: label(v, 60)),
        "source_is_exact_commit": _field(m.get("source_is_exact_commit"), bool),
        "fixture": _field(m.get("fixture"), lambda v: {"sha256": _hex(v.get("sha256"))}),
        "tasks": _field(m.get("tasks"), lambda v: {
            "schema_version": label(v.get("schema_version"), 8), "count": _num(v.get("count"), int),
            "sha256": _hex(v.get("sha256")), "ref": label(v.get("ref"), 60)}),
        "config": _field(m.get("config"), lambda v: {"sha256": _hex(v.get("sha256")), "ref": label(v.get("ref"), 40)}),
        "protocol": _field(m.get("protocol"), lambda v: {"ref": label(v.get("ref"), 80),
                                                         "basis": label(v.get("basis"), 60)}),
        "python": _field(m.get("python"), lambda v: {"version": label(v.get("version"), 20),
                                                     "implementation": label(v.get("implementation"), 20)}),
        "platform": _field(m.get("platform"), lambda v: {"system": label(v.get("system"), 20)}),
        "exec_mode": _field(m.get("exec_mode"), lambda v: _enum(v, ("sequential", "batch", "native"))),
        "adapter": {
            "provider": label(adapter.get("provider"), 40), "model": label(adapter.get("model")),
            "adapter_module": label(adapter.get("adapter_module"), 60),
            "sdk": _field(adapter.get("sdk"), lambda v: {"package": label(v.get("package"), 40),
                                                         "version": label(v.get("version"), 30)}),
            "model_version": _field(adapter.get("model_version"), lambda v: label(v, 60)),
        },
    }
    return out


def _manifest_commit(info):
    try:
        c = info["manifest"]["git"]["commit"]
        return c["value"] if c["status"] == "available" else None
    except Exception:  # noqa: BLE001
        return None


def _manifest_dirty(info):
    """True/False only when the manifest established it; otherwise None
    (unknown), never an assumed 'clean'."""
    try:
        w = info["manifest"]["git"]["worktree"]
        return (w["value"]["state"] == "dirty") if w["status"] == "available" else None
    except Exception:  # noqa: BLE001
        return info.get("code_dirty") if isinstance(info.get("code_dirty"), bool) else None


def safe_error(exc):
    """Run-level error for display: the exception TYPE only. The message can
    hold paths, keys or provider text and stays in the raw layer."""
    return {"error_type": label(type(exc).__name__, 60)}


def assert_safe(obj, _where="payload"):
    """Defence in depth: refuse anything that looks like raw evidence, a
    path or a credential. Called on every write to the live store."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise UnsafePayload(f"{_where}: non-string key")
            if k.lower() in FORBIDDEN_KEYS:
                raise UnsafePayload(f"{_where}: forbidden field {k!r}")
            assert_safe(v, f"{_where}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            assert_safe(v, f"{_where}[{i}]")
    elif isinstance(obj, str):
        if len(obj) > MAX_STRING:
            raise UnsafePayload(f"{_where}: string longer than {MAX_STRING} characters")
        if _SECRETISH.search(obj):
            raise UnsafePayload(f"{_where}: credential-like text")
        if _PATHISH.search(obj):
            raise UnsafePayload(f"{_where}: filesystem-path-like text")
    elif not (obj is None or isinstance(obj, (bool, int, float))):
        raise UnsafePayload(f"{_where}: unsupported type {type(obj).__name__}")
    return True
