"""Support scoping: waiver check runs only for support == 'waiver'."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from minilab.evaluation import evaluate  # noqa: E402
from minilab.tasks import TASKS, validate_task  # noqa: E402

ACCT = [{"account_id": "A103", "customer_id": "C102", "balance": 800}]


def _task(**kw):
    t = {"task_id": "probe", "difficulty": "easy", "customer_id": "C102",
         "prompt": "probe", "expected_eligible": True, "expected_rule": "none",
         "required_evidence": ["800"], "max_steps": 3,
         "required_tools": ["get_account"], "grounded_numbers": [800]}
    t.update(kw)
    validate_task(t)
    return t


def _traj(answer, steps):
    return {"task_id": "probe", "termination_reason": "agent_final",
            "final_answer": answer, "steps": steps}


class TestSupportScoping(unittest.TestCase):
    def test_waiver_tasks_opt_in(self):
        for t in TASKS:
            if t["task_id"].startswith("waiver-"):
                self.assertEqual(t.get("support"), "waiver")
            validate_task(t)

    def test_unknown_support_rejected(self):
        with self.assertRaises(ValueError):
            _task(support="fuzzy")
        _task(support="waiver")
        _task(support=None)
        _task()  # absent is fine

    def test_retrieval_without_policy_passes(self):
        """B: get_account + '800', no policy/finance context needed."""
        t = _task(support=None, expected_answer={"type": "number", "value": 800})
        steps = [{"action": "get_account", "arguments": {"customer_id": "C102"},
                  "observation": ACCT}]
        r = evaluate(t, _traj("Balance is 800.", steps))
        self.assertTrue(r["support_ok"], r["reasons"])
        self.assertTrue(r["passed"], r["reasons"])

    def test_calc_without_policy_passes(self):
        """C: account + calculate, no search_policy required or present."""
        t = _task(support=None, expected_answer={"type": "number", "value": 3000},
                  required_tools=["get_account", "calculate"],
                  required_evidence=["3000"], grounded_numbers=[1800, 1200, 3000])
        steps = [
            {"action": "get_account", "arguments": {"customer_id": "C101"},
             "observation": [{"account_id": "A101", "customer_id": "C101", "balance": 1800},
                             {"account_id": "A102", "customer_id": "C101", "balance": 1200}]},
            {"action": "calculate", "arguments": {"op": "sum", "values": [1800, 1200]},
             "observation": {"result": 3000}},
        ]
        r = evaluate(t, _traj("Combined total is 3000.", steps))
        self.assertTrue(r["passed"], r["reasons"])

    def test_answer_alone_does_not_ground(self):
        """D: matching expected_answer with no observation still fails."""
        t = _task(support=None, expected_answer={"type": "number", "value": 800})
        steps = [{"action": "get_account", "arguments": {"customer_id": "C102"},
                  "observation": []}]
        r = evaluate(t, _traj("Balance is 800.", steps))
        self.assertFalse(r["grounding_ok"])
        self.assertFalse(r["passed"])

    def test_missing_required_tool_still_fails(self):
        """E: support=None does not excuse skipping required tools."""
        t = _task(support=None, expected_answer={"type": "number", "value": 800})
        r = evaluate(t, _traj("Balance is 800.", []))
        self.assertFalse(r["grounding_ok"])
        self.assertIn("get_account", str(r["reasons"]))
        self.assertFalse(r["passed"])

    def test_waiver_support_still_fires_when_opted_in(self):
        """A: support='waiver' with contradicting finances still fails."""
        t = _task(support="waiver", expected_eligible=True, customer_id="C103",
                  required_tools=["get_account", "get_transactions", "search_policy"],
                  grounded_numbers=[300, 100])
        steps = [
            {"action": "get_account", "arguments": {"customer_id": "C103"},
             "observation": [{"account_id": "A104", "customer_id": "C103", "balance": 300}]},
            {"action": "get_transactions",
             "arguments": {"account_id": "A104", "last_n_days": 30},
             "observation": [{"type": "direct_deposit", "amount": 100, "days_ago": 5}]},
            {"action": "search_policy", "arguments": {"query": "waiver"},
             "observation": [{"policy_id": "WAIVER-01",
                              "text": "RULE-A average >= $1,500; RULE-B deposits >= $500"}]},
        ]
        r = evaluate(t, _traj("C103 qualifies with 300 and 100.", steps))
        self.assertFalse(r["support_ok"])
        self.assertFalse(r["passed"])


if __name__ == "__main__":
    unittest.main()
