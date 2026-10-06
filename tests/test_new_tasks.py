"""The five new tasks: definitions validate, oracles pass, traps fail."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from minilab.agent import run_agent  # noqa: E402
from minilab.env import MiniBankEnv  # noqa: E402
from minilab.evaluation import evaluate  # noqa: E402
from minilab.runner import make_model  # noqa: E402
from minilab.tasks import TASKS, get_task, validate_tasks  # noqa: E402

NEW = ["retrieval-bal-a103", "calc-total-c101", "policy-attr-c102",
       "temporal-deposit-a103", "horizon-checking-combined"]


def _scripted(tid):
    task = get_task(tid)
    traj = run_agent(task["task_id"], task["prompt"], MiniBankEnv(),
                     make_model("scripted", tid), max_steps=task["max_steps"])
    return task, traj, evaluate(task, traj)


class TestNewTasks(unittest.TestCase):
    def test_all_definitions_validate(self):
        """A: all eight tasks validate; new ids present with expected_answer."""
        validate_tasks(TASKS)
        self.assertEqual(len(TASKS), 8)
        for tid in NEW:
            self.assertIn("expected_answer", get_task(tid))

    def test_retrieval_passes(self):
        """B: correct get_account evidence passes."""
        task, traj, r = _scripted("retrieval-bal-a103")
        self.assertEqual(traj.termination_reason, "agent_final")
        self.assertTrue(r["passed"], r["reasons"])

    def test_retrieval_fails_without_account(self):
        """C: no get_account call fails."""
        task = get_task("retrieval-bal-a103")
        traj = {"task_id": task["task_id"], "termination_reason": "agent_final",
                "final_answer": "Balance is 800.", "steps": []}
        r = evaluate(task, traj)
        self.assertFalse(r["passed"])
        self.assertFalse(r["grounding_ok"])

    def test_calc_passes_with_tool(self):
        """D: calculate actually used passes."""
        task, traj, r = _scripted("calc-total-c101")
        self.assertTrue(r["passed"], r["reasons"])
        self.assertIn("calculate", {s.action for s in traj.steps})

    def test_calc_fails_without_tool(self):
        """E: right number, no calculate step, fails."""
        task = get_task("calc-total-c101")
        traj = {"task_id": task["task_id"], "termination_reason": "agent_final",
                "final_answer": "Combined total is 3000.",
                "steps": [{"action": "get_account", "arguments": {"customer_id": "C101"},
                           "observation": [{"account_id": "A101", "customer_id": "C101", "balance": 1800},
                                           {"account_id": "A102", "customer_id": "C101", "balance": 1200}]}]}
        r = evaluate(task, traj)
        self.assertTrue(r["verdict_match"])  # answer gate alone passes...
        self.assertFalse(r["passed"])  # ...but 3000 was never computed

    def test_policy_attr_passes(self):
        """F: correct policy + financial evidence passes."""
        task, traj, r = _scripted("policy-attr-c102")
        self.assertTrue(r["passed"], r["reasons"])
        self.assertTrue(r["support_ok"], r["reasons"])

    def test_temporal_accepts_600(self):
        """G: 30-day total passes; T003 correctly ignored."""
        task, traj, r = _scripted("temporal-deposit-a103")
        self.assertTrue(r["passed"], r["reasons"])
        obs = [s.observation for s in traj.steps if s.action == "calculate"]
        self.assertEqual(obs, [{"result": 600}])

    def test_horizon_accepts_2600(self):
        """H: checking-only combined total passes."""
        task, traj, r = _scripted("horizon-checking-combined")
        self.assertTrue(r["passed"], r["reasons"])
        self.assertEqual(len(traj.steps), 7)

    def test_horizon_rejects_3800(self):
        """I: savings-included total fails."""
        task = get_task("horizon-checking-combined")
        traj = {"task_id": task["task_id"], "termination_reason": "agent_final",
                "final_answer": "Combined balance is 3800 (A101 1800 + A102 1200 + A103 800).",
                "steps": [
                    {"action": "get_customer", "arguments": {"customer_id": "C101"},
                     "observation": {"customer_id": "C101"}},
                    {"action": "get_account", "arguments": {"customer_id": "C101"},
                     "observation": [{"account_id": "A101", "customer_id": "C101", "balance": 1800},
                                     {"account_id": "A102", "customer_id": "C101", "balance": 1200}]},
                    {"action": "get_account", "arguments": {"customer_id": "C102"},
                     "observation": [{"account_id": "A103", "customer_id": "C102", "balance": 800}]},
                    {"action": "calculate", "arguments": {"op": "sum", "values": [1800, 1200, 800]},
                     "observation": {"result": 3800}},
                ]}
        r = evaluate(task, traj)
        self.assertFalse(r["verdict_match"])
        self.assertFalse(r["passed"])


if __name__ == "__main__":
    unittest.main()
