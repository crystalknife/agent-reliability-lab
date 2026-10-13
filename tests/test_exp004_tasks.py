"""EXP-004 candidate tasks: definitions validate, oracles pass, duplicates harmless."""

import copy
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

NEW = ["deposit-total-combined", "retrieval-multi-checking"]

DATA_TOOLS = ("get_account", "get_transactions", "calculate")


def _scripted(tid):
    task = get_task(tid)
    traj = run_agent(task["task_id"], task["prompt"], MiniBankEnv(),
                     make_model("scripted", tid), max_steps=task["max_steps"])
    return task, traj, evaluate(task, traj)


def _with_duplicated_observations(traj, times):
    """Repeat each data-tool step `times` extra (×2 for times=1, ×3 for times=2)."""
    steps = []
    for s in traj.steps:
        d = {"action": s.action, "arguments": copy.deepcopy(s.arguments),
             "observation": copy.deepcopy(s.observation)}
        steps.append(d)
        if s.action in DATA_TOOLS:
            for _ in range(times):
                steps.append(copy.deepcopy(d))
    return {"task_id": traj.task_id, "termination_reason": traj.termination_reason,
            "final_answer": traj.final_answer, "steps": steps}


class TestExp004Tasks(unittest.TestCase):
    def test_definitions_validate(self):
        """T0: all ten tasks validate; new ids present with expected_answer."""
        validate_tasks(TASKS)
        self.assertEqual(len(TASKS), 10)
        for tid in NEW:
            self.assertIn("expected_answer", get_task(tid))

    def test_oracles_pass(self):
        """T1: scripted oracle trajectory for each new task passes evaluation."""
        for tid in NEW:
            task, traj, r = _scripted(tid)
            self.assertEqual(traj.termination_reason, "agent_final", tid)
            self.assertTrue(r["passed"], f"{tid}: {r['reasons']}")
            self.assertTrue(r["support_ok"], f"{tid}: {r['reasons']}")

    def test_oracle_budgets_follow_convention(self):
        """T1b: max_steps is oracle invocations (calls + final) + 1 slack."""
        from minilab.runner import _ORACLE
        for tid in NEW:
            task = get_task(tid)
            self.assertEqual(task["max_steps"], len(_ORACLE[tid][0]) + 1 + 1, tid)

    def test_duplicated_observations_harmless(self):
        """T2: duplicating data-tool observations changes nothing."""
        for tid in NEW:
            task, traj, base = _scripted(tid)
            self.assertTrue(base["passed"])
            dup = evaluate(task, _with_duplicated_observations(traj, 1))
            self.assertEqual(dup["passed"], base["passed"], tid)
            self.assertEqual(dup["verdict_match"], base["verdict_match"], tid)
            self.assertEqual(dup["support_ok"], base["support_ok"], tid)

    def test_triplicated_observations_harmless(self):
        """T3: triplicating data-tool observations changes nothing."""
        for tid in NEW:
            task, traj, base = _scripted(tid)
            trip = evaluate(task, _with_duplicated_observations(traj, 2))
            self.assertEqual(trip["passed"], base["passed"], tid)
            self.assertEqual(trip["verdict_match"], base["verdict_match"], tid)
            self.assertEqual(trip["support_ok"], base["support_ok"], tid)


if __name__ == "__main__":
    unittest.main()
