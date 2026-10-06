"""Export MiniLab task definitions for the observability frontend.

Read-only over src/minilab/tasks.py: dumps task fields to JSON so the
frontend can describe the suite without duplicating ground truth.
tasks.py remains the single source of truth; this file only serializes it.

Usage: python scripts/export_task_catalog.py <output-json>
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from minilab.tasks import TASKS, validate_tasks  # noqa: E402

# Editorial capability labels (presentation only, not benchmark semantics).
FAMILY = {
    "waiver-c101": "eligibility verdict",
    "waiver-c102": "eligibility verdict",
    "waiver-c103": "eligibility verdict",
    "retrieval-bal-a103": "retrieval",
    "calc-total-c101": "calculation",
    "policy-attr-c102": "policy reasoning",
    "temporal-deposit-a103": "temporal transaction reasoning",
    "horizon-checking-combined": "multi-entity longer-horizon reasoning",
}

FIELDS = (
    "task_id", "difficulty", "prompt", "customer_id", "max_steps",
    "required_tools", "required_evidence", "grounded_numbers",
    "expected_eligible", "expected_rule", "support", "expected_answer",
)


def main() -> None:
    validate_tasks(TASKS)
    catalog = []
    for t in TASKS:
        entry = {k: t.get(k) for k in FIELDS}
        entry["family"] = FAMILY.get(t["task_id"], "general")
        catalog.append(entry)
    out = Path(sys.argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(catalog, indent=1), encoding="utf-8")
    print(f"wrote {len(catalog)} tasks to {out}")


if __name__ == "__main__":
    main()
