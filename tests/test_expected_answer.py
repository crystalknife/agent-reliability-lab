"""expected_answer gate. Synthetic task dicts only; no benchmark tasks added."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from minilab.evaluation import check_expected_answer, evaluate  # noqa: E402
from minilab.failures import classify  # noqa: E402
from minilab.tasks import TASKS, validate_task  # noqa: E402

BASE = {
    "task_id": "probe", "difficulty": "easy", "customer_id": "C101",
    "prompt": "probe", "expected_eligible": True, "expected_rule": "none",
    "required_evidence": ["total"], "max_steps": 3,
    "required_tools": ["get_transactions"], "grounded_numbers": [],
}
STEPS = [{
    "action": "get_transactions", "arguments": {"account_id": "A101"},
    "observation": [{"type": "direct_deposit", "amount": 600, "days_ago": 5}],
}]


def _task(**kw):
    t = dict(BASE)
    t.update(kw)
    validate_task(t)
    return t


def _traj(answer):
    return {"task_id": "probe", "termination_reason": "agent_final",
            "final_answer": answer, "steps": STEPS}


class TestExpectedAnswer(unittest.TestCase):
    def test_number_correct(self):
        t = _task(expected_answer={"type": "number", "value": 600})
        self.assertTrue(evaluate(t, _traj("Total deposits: 600."))["passed"])

    def test_number_incorrect(self):
        t = _task(expected_answer={"type": "number", "value": 600})
        r = evaluate(t, _traj("Total deposits: 500."))
        self.assertFalse(r["passed"])
        self.assertFalse(r["verdict_match"])
        self.assertEqual(classify(t, _traj("Total deposits: 500."), r), "policy_misread")

    def test_number_comma_format(self):
        t = _task(expected_answer={"type": "number", "value": 1500})
        self.assertTrue(check_expected_answer(t["expected_answer"], "average $1,500."))

    def test_enum_correct(self):
        t = _task(expected_answer={"type": "enum", "values": ["YES", "NO"], "expected": "YES"},
                  required_evidence=["yes"])
        self.assertTrue(evaluate(t, _traj("YES"))["passed"])

    def test_enum_incorrect(self):
        t = _task(expected_answer={"type": "enum", "values": ["YES", "NO"], "expected": "YES"},
                  required_evidence=["yes"])
        r = evaluate(t, _traj("NO"))
        self.assertFalse(r["passed"])
        self.assertEqual(classify(t, _traj("NO"), r), "policy_misread")

    def test_enum_rejects_sentence(self):
        self.assertFalse(check_expected_answer(
            {"type": "enum", "values": ["YES", "NO"], "expected": "YES"},
            "yes, it qualifies"))

    def test_text_correct(self):
        t = _task(expected_answer={"type": "text", "contains": ["RULE-A", "1500"]},
                  required_evidence=["rule"])
        self.assertTrue(evaluate(t, _traj("RULE-A average is $1,500."))["passed"])

    def test_text_incorrect(self):
        t = _task(expected_answer={"type": "text", "contains": ["RULE-A", "1500"]},
                  required_evidence=["rule"])
        r = evaluate(t, _traj("RULE-B deposits are 600."))
        self.assertFalse(r["passed"])

    def test_missing_keeps_waiver_path(self):
        for t in TASKS:
            if t["task_id"].startswith("waiver-"):
                self.assertNotIn("expected_answer", t)
            validate_task(t)

    def test_answer_alone_does_not_ground(self):
        """A number in the final answer is ungrounded without an observation."""
        t = _task(expected_answer={"type": "number", "value": 999},
                  grounded_numbers=[999])
        r = evaluate(t, _traj("Total: 999."))
        self.assertTrue(r["verdict_match"])  # answer gate passes...
        self.assertFalse(r["grounding_ok"])  # ...but nothing observed it
        self.assertFalse(r["passed"])

    def test_bad_specs_rejected(self):
        for spec in ({"type": "number"}, {"type": "number", "value": "x"},
                     {"type": "enum", "values": [], "expected": "YES"},
                     {"type": "enum", "values": ["YES"], "expected": "NO"},
                     {"type": "text"}, {"type": "text", "contains": []},
                     {"type": "regex", "pattern": ".*"}):
            with self.assertRaises(ValueError, msg=str(spec)):
                _task(expected_answer=spec)


if __name__ == "__main__":
    unittest.main()
