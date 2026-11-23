"""Aggregated analytics over sanitized trial summaries (layer 3).

Rules this module follows:
  * A pass rate is reported only over VALID trials (trial_status == "valid");
    provider/environment/evaluator errors are counted separately and never
    enter a denominator.
  * No rate without a denominator: with zero valid trials the rate is null.
  * An interval is shown only when there are at least MIN_VALID_FOR_INTERVAL
    valid trials. That number is a DISPLAY convention of this dashboard, not
    a statistical rule; below it the UI says "insufficient sample".
  * An interval is shown only when its independence assumption is
    defensible from the data's structure: at most one valid trial per task.
    With repeated trials of a task (clustering) the interval is SUPPRESSED,
    not merely caveated, because a Wilson interval would then overstate
    certainty. The rate is still shown.
  * A comparison of two runs is DESCRIPTIVE: the difference in pass rates.
    A Newcombe-Wilson interval is added only when the two runs share no
    task and neither repeats a task. Runs that share tasks are paired by
    task; an independent-samples interval is not a valid paired interval,
    so none is shown. No paired or cluster-robust estimator is implemented
    (that needs a design decision about the unit of analysis).
  * Nothing here says "significant" and nothing is an acceptance rule.
  * A figure computed from a dashboard record that is not known to be
    complete (incomplete, unavailable, in progress, or not supplied) carries
    a recording caveat: some trials may be missing from its denominator.
  * Depth and family are reported only when declared. Legacy schema-1 tasks
    have no declared depth; the two unreviewed candidate depths are returned
    as candidates, labelled as such, and are not used for grouping.
"""

from __future__ import annotations

from .. import legacy_anchors
from ..stats import INDEPENDENCE_CAVEAT, newcombe_wilson_difference, wilson_interval_or_none
from ..tasks import TASKS

MIN_VALID_FOR_INTERVAL = 5
CLUSTER_CAVEAT = ("interval not shown: trials are clustered by task (repeats of the same task are "
                  "not independent), so an independence-based interval would overstate certainty")
SHARED_TASK_CAVEAT = ("interval not shown: the two runs share tasks, so they are paired by task, not "
                      "independent samples; an independent-samples interval is not valid here")
DESCRIPTIVE_NOTE = "descriptive only: not a significance test and not an acceptance rule"
_LEGACY = {t["task_id"]: t for t in TASKS}
_CANDIDATE_DEPTHS = None


def task_info(task_id):
    """What is known about a task's structure. Declared depth/family exist
    only for schema-2 tasks registered with the suite; none exists yet."""
    global _CANDIDATE_DEPTHS
    if task_id in _LEGACY:
        if _CANDIDATE_DEPTHS is None:
            _CANDIDATE_DEPTHS = {n: legacy_anchors.candidate_depths(n) for n in legacy_anchors.CANDIDATE_TABLES}
        return {
            "task_id": task_id, "schema_version": "1", "family": None, "declared_depth": None,
            "depth_status": "undeclared: legacy schema-1 task; candidate graphs are unreviewed",
            "candidate_depths": {n: d[task_id] for n, d in _CANDIDATE_DEPTHS.items()},
            "difficulty": _LEGACY[task_id]["difficulty"],
        }
    return {"task_id": task_id, "schema_version": None, "family": None, "declared_depth": None,
            "depth_status": "unknown task: not in the registered suite", "candidate_depths": None,
            "difficulty": None}


def rate_block(k, n, n_tasks=None):
    """Pass count over valid trials, with an interval only when justified."""
    block = {"passed": k, "valid": n, "rate": (k / n) if n else None, "interval": None,
             "interval_suppressed": None,
             "insufficient_sample": n < MIN_VALID_FOR_INTERVAL, "notes": []}
    if n == 0:
        block["notes"].append("no valid trials: pass rate undefined")
        return block
    if n < MIN_VALID_FOR_INTERVAL:
        block["notes"].append(
            f"fewer than {MIN_VALID_FOR_INTERVAL} valid trials: interval not shown (display convention)")
        return block
    if n_tasks is not None and n_tasks < n:
        block["interval_suppressed"] = "clustered_by_task"
        block["notes"].append(CLUSTER_CAVEAT)
        return block
    w = wilson_interval_or_none(k, n)
    block["interval"] = {"lower": w.lower, "upper": w.upper, "confidence": w.confidence, "method": "wilson"}
    return block


def _valid(trials):
    return [t for t in trials if t.get("state") == "finished" and t.get("trial_status") == "valid"]


def recording_caveat(recording):
    """None only when the dashboard record is known to be complete."""
    status = (recording or {}).get("status")
    if status == "complete":
        return None
    if status == "in_progress":
        return "recording in progress: figures cover only the trials recorded so far"
    if status in ("incomplete", "unavailable"):
        return (f"recording {status}: trials may be missing from this figure; "
                "the JSONL result files are the authoritative evidence")
    return "recording completeness not established: trials may be missing from this figure"


def _tally(items, key):
    out = {}
    for t in items:
        v = key(t)
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items()))


def experiment_analytics(trials, recording=None):
    caveat = recording_caveat(recording)
    finished = [t for t in trials if t.get("state") == "finished"]
    valid = _valid(trials)
    passed = [t for t in valid if t.get("passed") is True]
    failed = [t for t in valid if t.get("passed") is not True]
    by_task = {}
    for t in valid:
        by_task.setdefault(t["task_id"], []).append(t)
    tasks_seen = sorted({t["task_id"] for t in trials})

    per_task = []
    for tid in tasks_seen:
        vs = by_task.get(tid, [])
        k = sum(1 for t in vs if t.get("passed") is True)
        info = task_info(tid)
        per_task.append({**info, "trials": sum(1 for t in trials if t["task_id"] == tid),
                         "excluded": sum(1 for t in finished if t["task_id"] == tid
                                         and t.get("trial_status") != "valid"),
                         **{f: v for f, v in rate_block(k, len(vs), n_tasks=1).items()}})

    by_difficulty = {}
    for diff in sorted({t.get("difficulty") or "unknown" for t in valid}):
        vs = [t for t in valid if (t.get("difficulty") or "unknown") == diff]
        by_difficulty[diff] = rate_block(sum(1 for t in vs if t.get("passed") is True), len(vs),
                                         n_tasks=len({t["task_id"] for t in vs}))

    declared = {tid: task_info(tid)["declared_depth"] for tid in tasks_seen}
    if any(d is not None for d in declared.values()):
        by_depth = {"available": True, "groups": {}}
        for depth in sorted({d for d in declared.values() if d is not None}):
            vs = [t for t in valid if declared[t["task_id"]] == depth]
            by_depth["groups"][str(depth)] = rate_block(
                sum(1 for t in vs if t.get("passed") is True), len(vs),
                n_tasks=len({t["task_id"] for t in vs}))
    else:
        by_depth = {"available": False,
                    "reason": "no task in this run has a declared depth (legacy schema-1 tasks carry "
                              "none; the candidate graphs are unreviewed and not used for grouping)"}
    by_family = {"available": False, "reason": "no task family is defined for the tasks in this run"}

    prov = {"verified": 0, "indeterminate": 0, "not_observable": 0, "unavailable_trials": 0}
    for t in finished:
        p = t.get("provenance") or {}
        if not p.get("available"):
            prov["unavailable_trials"] += 1
            continue
        for s, n in (p.get("completeness") or {}).items():
            prov[s] = prov.get(s, 0) + n

    pass_rate = rate_block(len(passed), len(valid), n_tasks=len(by_task))
    if caveat:
        pass_rate["notes"].append(caveat)
    return {
        "recording_status": (recording or {}).get("status"), "recording_caveat": caveat,
        "counts": {
            "trials_started": len(trials), "trials_finished": len(finished),
            "trials_running": sum(1 for t in trials if t.get("state") == "running"),
            "trials_unrecorded": sum(1 for t in trials if t.get("state") == "unrecorded"),
            "valid": len(valid), "excluded": len(finished) - len(valid),
            "by_trial_status": _tally(finished, lambda t: t.get("trial_status") or "unknown"),
            "passed": len(passed), "failed_valid": len(failed), "tasks": len(tasks_seen),
        },
        "pass_rate": pass_rate,
        "failure_distribution": _tally(failed, lambda t: t.get("failure") or "unknown"),
        "reason_categories": _tally([{"c": c} for t in failed for c in t.get("reason_categories", [])],
                                    lambda x: x["c"]),
        "termination_reasons": _tally(finished, lambda t: t.get("termination_reason") or "unknown"),
        "by_task": per_task, "by_difficulty": by_difficulty, "by_depth": by_depth, "by_family": by_family,
        "tool_calls_total": sum(t.get("tool_calls") or 0 for t in finished),
        "model_requests_total": sum(t.get("model_requests") or 0 for t in finished),
        "provenance_completeness": prov,
        "min_valid_for_interval": MIN_VALID_FOR_INTERVAL,
    }


def compare_experiments(trials_a, trials_b, recording_a=None, recording_b=None):
    """Descriptive difference of valid-trial pass rates, A minus B."""
    caveats = {arm: c for arm, c in (("a", recording_caveat(recording_a)),
                                     ("b", recording_caveat(recording_b))) if c}
    va, vb = _valid(trials_a), _valid(trials_b)
    ka = sum(1 for t in va if t.get("passed") is True)
    kb = sum(1 for t in vb if t.get("passed") is True)
    shared = sorted({t["task_id"] for t in va} & {t["task_id"] for t in vb})
    tasks_a, tasks_b = {t["task_id"] for t in va}, {t["task_id"] for t in vb}
    clustered = len(tasks_a) < len(va) or len(tasks_b) < len(vb)
    out = {"kind": "descriptive",
           "a": rate_block(ka, len(va), n_tasks=len(tasks_a)),
           "b": rate_block(kb, len(vb), n_tasks=len(tasks_b)),
           "shared_tasks": len(shared), "difference": None, "interval": None,
           "interval_suppressed": None,
           "insufficient_sample": min(len(va), len(vb)) < MIN_VALID_FOR_INTERVAL,
           "recording": {"a": (recording_a or {}).get("status"), "b": (recording_b or {}).get("status")},
           "recording_caveat": caveats or None,
           "notes": [DESCRIPTIVE_NOTE] + [f"run {arm.upper()}: {c}" for arm, c in caveats.items()]}
    if not va or not vb:
        out["interval_suppressed"] = "no_valid_trials"
        out["notes"].append("one arm has no valid trials: difference undefined")
        return out
    out["difference"] = ka / len(va) - kb / len(vb)
    if shared:
        out["interval_suppressed"] = "shared_tasks"
        out["notes"].append(SHARED_TASK_CAVEAT + " (the two runs share tasks)")
    elif clustered:
        out["interval_suppressed"] = "clustered_by_task"
        out["notes"].append(CLUSTER_CAVEAT)
    elif out["insufficient_sample"]:
        out["interval_suppressed"] = "insufficient_sample"
        out["notes"].append(
            f"an arm has fewer than {MIN_VALID_FOR_INTERVAL} valid trials: interval not shown "
            "(display convention, not a statistical rule)")
    else:
        d = newcombe_wilson_difference(ka, len(va), kb, len(vb))
        out["interval"] = {"lower": d.lower, "upper": d.upper, "confidence": d.confidence,
                           "method": d.method}
        out["notes"].append(INDEPENDENCE_CAVEAT)
    return out
