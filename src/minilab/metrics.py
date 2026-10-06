"""Reliability metrics. Pure aggregation over results JSONL."""

import argparse
import json
import statistics
from pathlib import Path


def load_results(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _rate(rows):
    if not rows:
        return {"n": 0, "success_rate": 0.0, "std": 0.0}
    xs = [1.0 if r["passed"] else 0.0 for r in rows]
    return {
        "n": len(rows),
        "success_rate": sum(xs) / len(xs),
        "std": statistics.pstdev(xs) if len(xs) > 1 else 0.0,
    }


def summarize(rows):
    by_task, by_difficulty, failures = {}, {}, {}
    trial_status = {}
    valid = []
    for r in rows:
        by_task.setdefault(r["task_id"], []).append(r)
        by_difficulty.setdefault(r.get("difficulty", "?"), []).append(r)
        # Agent failure taxonomy counts only valid trials: a provider_error
        # carries no agent behavior to attribute.
        if _status_of(r) == "valid":
            f = r.get("failure", "?")
            failures[f] = failures.get(f, 0) + 1
            valid.append(r)
        s = _status_of(r)
        trial_status[s] = trial_status.get(s, 0) + 1
    return {
        "overall": _rate(rows),
        "trials": {
            "total": len(rows),
            "valid": len(valid),
            "by_status": trial_status,
            "provider_errors": trial_status.get("provider_error", 0),
        },
        "agent_reliability": _rate(valid),
        "by_task": {k: _rate(v) for k, v in sorted(by_task.items())},
        "by_difficulty": {k: _rate(v) for k, v in sorted(by_difficulty.items())},
        "failures": failures,
    }


def _status_of(row):
    """Trial status. Absent on rows written before statuses existed."""
    return row.get("trial_status", "valid")


def format_text(summary):
    t = summary["trials"]
    lines = [
        f"trials: total={t['total']} valid={t['valid']} "
        f"provider_errors={t['provider_errors']}",
        f"trial_status: {t['by_status']}",
        f"agent_reliability (valid trials only): {summary['agent_reliability']}",
    ]
    for k, v in summary["by_task"].items():
        lines.append(f"task {k}: {v}")
    for k, v in summary["by_difficulty"].items():
        lines.append(f"difficulty {k}: {v}")
    lines.append(f"failures (valid trials only): {summary['failures']}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results/results.jsonl")
    a = ap.parse_args()
    print(format_text(summarize(load_results(a.results))))


if __name__ == "__main__":
    main()
