"""Smoke tests. Stdlib only: python -m unittest discover -s tests -v."""

import inspect
import json
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from minilab.agent import run_agent  # noqa: E402
from minilab.env import MiniBankEnv  # noqa: E402
from minilab.evaluation import evaluate  # noqa: E402
from minilab.failures import classify, classify_trial  # noqa: E402
from minilab.metrics import summarize  # noqa: E402
from minilab.runner import run_experiment, make_model, _ORACLE  # noqa: E402
from minilab.tasks import TASKS, get_task, validate_task  # noqa: E402
from minilab.tools import TOOL_NAMES, dispatch  # noqa: E402
from minilab.trajectory import Trajectory, load_jsonl  # noqa: E402


class TestLab(unittest.TestCase):
    def test_env_loads(self):
        env = MiniBankEnv()
        self.assertEqual(env.get_customer("C101")["name"], "Alice Rivera")
        self.assertIn("error", env.get_customer("ZZZ"))

    def test_tools(self):
        env = MiniBankEnv()
        accs = dispatch(env, "get_account", {"customer_id": "C101"})
        self.assertEqual(len(accs), 2)
        self.assertEqual(dispatch(env, "calculate", {"op": "avg", "values": [1800, 1200]}), {"result": 1500.0})
        self.assertIn("error", dispatch(env, "calculate", {"op": "bogus", "values": [1]}))
        with self.assertRaises(ValueError):
            dispatch(env, "nope", {})
        self.assertEqual(set(TOOL_NAMES), {"get_customer", "get_account", "get_transactions", "search_policy", "calculate"})

    def test_trajectory_roundtrip(self):
        t = Trajectory(task_id="waiver-c101", steps=[], final_answer="x", termination_reason="agent_final")
        d = t.to_dict()
        self.assertEqual(Trajectory.from_dict(json.loads(json.dumps(d))).task_id, "waiver-c101")

    def _run(self, task_id, model, **kw):
        model_fn = make_model(model, task_id)
        def wrapped(messages, tools, config):
            return model_fn(messages, tools, config)
        task = get_task(task_id)
        return run_agent(task_id, task["prompt"], MiniBankEnv(), wrapped, **kw)

    def test_scripted_passes(self):
        for task in TASKS:
            traj = self._run(task["task_id"], "scripted")
            self.assertEqual(traj.termination_reason, "agent_final", task["task_id"])
            v = evaluate(task, traj)
            self.assertTrue(v["passed"], f"{task['task_id']}: {v['reasons']}")
            self.assertEqual(classify(task, traj, v), "none")
            for i, s in enumerate(traj.steps, 1):
                self.assertEqual(s.step, i)
                for attr in ("action", "arguments", "observation"):
                    self.assertTrue(hasattr(s, attr))

    def test_stub_fails_with_evidence_gap(self):
        task = get_task("waiver-c102")
        traj = self._run("waiver-c102", "stub")
        v = evaluate(task, traj)
        self.assertFalse(v["passed"])
        self.assertEqual(classify(task, traj, v), "evidence_gap")

    def test_terminations(self):
        task = get_task("waiver-c101")
        env = MiniBankEnv()
        bad = run_agent("waiver-c101", task["prompt"], env, lambda m, t, c: {"garbage": 1})
        self.assertEqual(bad.termination_reason, "invalid_action")
        evil = run_agent("waiver-c101", task["prompt"], env,
                         lambda m, t, c: {"tool": "nope", "arguments": {}})
        self.assertEqual(evil.termination_reason, "tool_error")
        slow = run_agent("waiver-c101", task["prompt"], env,
                         lambda m, t, c: {"tool": "search_policy", "arguments": {"query": "waiver"}},
                         max_steps=2)
        self.assertEqual(slow.termination_reason, "max_steps")
        v = evaluate(task, slow)
        self.assertEqual(classify(task, slow, v), "premature_stop")

    def test_runner_writes_config_and_metrics(self):
        with tempfile.TemporaryDirectory() as td:
            rows = run_experiment(["waiver-c101"], repeats=2, seed=0, model="scripted",
                                  results_path=f"{td}/r.jsonl", trajectories_path=f"{td}/t.jsonl")
            self.assertEqual(len(rows), 2)
            for r in rows:
                for f in ("run_id", "experiment_id", "timestamp", "seed", "model", "provider", "temperature",
                          "max_steps", "task_id", "difficulty", "passed", "failure",
                          "termination_reason", "reasons"):
                    self.assertIn(f, r)
            s = summarize(rows)
            self.assertEqual(s["overall"]["success_rate"], 1.0)
            self.assertEqual(s["by_difficulty"]["easy"]["n"], 2)

    # --- Regression tests for measurement integrity (E1-E7) ---

    def test_zero_tool_false_pass_is_now_blocked(self):
        # Previously, an agent could answer correctly without using any tools by
        # memorizing the policy text. Now, grounding requires tool usage.
        task = get_task("waiver-c101")
        # Create a model that outputs a correct-sounding answer without tool use.
        def memo_model(messages, tools, config):
            return {"final": "Customer C101 qualifies for the fee waiver under WAIVER-01 RULE-A because the average balance is 1500."}
        traj = run_agent(task["task_id"], task["prompt"], MiniBankEnv(), memo_model, max_steps=1)
        # The agent did not call any tools.
        self.assertEqual(len(traj.steps), 0)
        # The answer is textually correct but should fail because no tools used.
        v = evaluate(task, traj)
        self.assertFalse(v["passed"], msg=f"Expected fail but got: {v}")
        # Should be classified as evidence_gap (or grounding failure) because no tool calls.
        self.assertIn(classify(task, traj, v), ("evidence_gap",))

    def test_unsupported_numeric_even_if_mentioned_is_blocked(self):
        # The agent might mention a number that appears in the policy text but not in
        # the data (e.g., the threshold 1500 for C102, which is not in the data).
        task = get_task("waiver-c102")
        # For C102, the grounded number is 600 (observed 30-day deposit). The policy
        # threshold 500 is not in the data, but the agent might still mention it.
        def model_that_mentions_threshold(messages, tools, config):
            # If the agent has not yet called get_transactions, we let it proceed.
            # We'll just return a final answer that mentions the threshold.
            return {"final": "Customer C102 qualifies because the policy says 500 is the threshold and they have 600."}
        traj = run_agent(task["task_id"], task["prompt"], MiniBankEnv(), model_that_mentions_threshold, max_steps=1)
        v = evaluate(task, traj)
        # The answer mentions 600 (which is not grounded because we didn't call the tool)
        # and 500 (which is not in the data). The evaluator should see that the
        # required number 600 is not present in any data-tool observation.
        self.assertFalse(v["passed"], msg=f"Expected fail but got: {v}")
        self.assertIn(classify(task, traj, v), ("evidence_gap",))

    def test_correct_verdict_with_natural_paraphrasing_passes(self):
        task = get_task("waiver-c101")
        # Use the scripted model which gives a correct, grounded answer.
        traj = self._run(task["task_id"], "scripted")
        # The scripted model's answer is: "C101 qualifies for the fee waiver under WAIVER-01 RULE-A. "
        # "Average balance (1800+1200)/2 = 1500, which meets the >= 1500 threshold."
        # This should pass with our new verdict recognition.
        v = evaluate(task, traj)
        self.assertTrue(v["passed"], msg=f"Expected pass but got: {v}")
        self.assertEqual(classify(task, traj, v), "none")

    def test_negative_verdict_with_contractions_passes(self):
        task = get_task("waiver-c103")
        # For C103, the correct verdict is that they do NOT qualify.
        # We'll create a model that uses a contraction.
        def make_negative_model():
            calls, final = _ORACLE[task["task_id"]]
            # Replace "does not" with "doesn't" in the final answer.
            contracted_final = final.replace("does not", "doesn't")
            state = {"i": 0}
            def model_fn(messages, tools, config):
                if state["i"] < len(calls):
                    tool, args = calls[state["i"]]
                    state["i"] += 1
                    return {"tool": tool, "arguments": args}
                return {"final": contracted_final}
            return model_fn
        traj = run_agent(task["task_id"], task["prompt"], MiniBankEnv(), make_negative_model(), max_steps=task["max_steps"])
        v = evaluate(task, traj)
        self.assertTrue(v["passed"], msg=f"Expected pass but got: {v}")
        self.assertEqual(classify(task, traj, v), "none")

    def test_hedged_verdict_is_treated_as_mismatch(self):
        task = get_task("waiver-c101")
        # Use the scripted model but hedge the verdict.
        def make_hedged_model():
            calls, final = _ORACLE[task["task_id"]]
            hedged_final = f"It may be that {final}"
            state = {"i": 0}
            def model_fn(messages, tools, config):
                if state["i"] < len(calls):
                    tool, args = calls[state["i"]]
                    state["i"] += 1
                    return {"tool": tool, "arguments": args}
                return {"final": hedged_final}
            return model_fn
        traj = run_agent(task["task_id"], task["prompt"], MiniBankEnv(), make_hedged_model(), max_steps=task["max_steps"])
        v = evaluate(task, traj)
        # The verdict is hedged, so it should not match the expected positive verdict.
        self.assertFalse(v["passed"], msg=f"Expected fail but got: {v}")
        # It should be classified as policy_misread (verdict mismatch) because the
        # hedged language is not recognized as a positive verdict.
        self.assertEqual(classify(task, traj, v), "policy_misread")

    def test_model_exception_does_not_crash_experiment(self):
        task = get_task("waiver-c101")
        # A model that raises an exception on the first call.
        def failing_model(messages, tools, config):
            raise ValueError("model API error")
        traj = run_agent(task["task_id"], task["prompt"], MiniBankEnv(), failing_model, max_steps=2)
        # The loop should have caught the exception and set termination_reason to model_error.
        self.assertEqual(traj.termination_reason, "model_error")
        # The evaluator should mark it as failed.
        v = evaluate(task, traj)
        self.assertFalse(v["passed"])
        # A model fault is a provider_error at trial level, never an agent failure.
        self.assertEqual(classify_trial(traj), "provider_error")
        self.assertEqual(classify(task, traj, v), "none")
        # The runner should have written a row (we can't check here, but the test passes if no exception).

    def test_tool_exception_does_not_crash_experiment(self):
        task = get_task("waiver-c101")
        # We'll monkey-patch dispatch to raise on a specific tool call.
        original_dispatch = dispatch
        def failing_dispatch(env, name, args):
            if name == "get_account":
                raise ValueError("simulated tool error")
            return original_dispatch(env, name, args)
        # We need to inject this into the agent's environment? Actually, the agent
        # calls dispatch from tools.py. We'll instead create a model that calls
        # get_account and let the real dispatch raise, but we don't want to modify
        # the tools module. Instead, we'll use an environment that raises.
        # Let's create a subclass of MiniBankEnv that raises on get_account.
        class FailingEnv(MiniBankEnv):
            def get_account(self, customer_id=None, account_id=None):
                if customer_id == "C101" or account_id == "A101":
                    raise ValueError("simulated tool error")
                return super().get_account(customer_id, account_id)
        env = FailingEnv()
        # Use a model that will call get_account.
        def model_that_calls_get_account(messages, tools, config):
            # We'll just return a tool call on the first step.
            return {"tool": "get_account", "arguments": {"customer_id": "C101"}}
        traj = run_agent(task["task_id"], task["prompt"], env, model_that_calls_get_account, max_steps=2)
        # The loop should have caught the exception from dispatch and set termination_reason to tool_error.
        self.assertEqual(traj.termination_reason, "tool_error")
        v = evaluate(task, traj)
        self.assertFalse(v["passed"])
        self.assertEqual(classify(task, traj, v), "tool_misuse")

    def test_malformed_task_fails_fast(self):
        # Test that validate_task catches a malformed task.
        bad_task = {
            "task_id": "bad",
            # missing difficulty, customer_id, prompt, etc.
        }
        with self.assertRaises(ValueError):
            validate_task(bad_task)
        # Also test that get_task raises KeyError for unknown task.
        with self.assertRaises(KeyError):
            get_task("not-a-task")

    def test_trajectory_and_result_share_run_id(self):
        with tempfile.TemporaryDirectory() as td:
            rows = run_experiment(["waiver-c101"], repeats=1, seed=0, model="scripted",
                                  results_path=f"{td}/r.jsonl", trajectories_path=f"{td}/t.jsonl")
            self.assertEqual(len(rows), 1)
            row = rows[0]
            # Load the trajectory file and check that its run_id matches.
            trajs = list(load_jsonl(f"{td}/t.jsonl"))
            self.assertEqual(len(trajs), 1)
            traj = trajs[0]
            self.assertEqual(traj.run_id, row["run_id"])
            # Also check that the experiment_id matches.
            self.assertEqual(traj.experiment_id, row["experiment_id"])

    def test_run_id_is_globally_unique_across_experiments(self):
        """Two separate experiments must never collide on run_id, even with the
        identical task/seed/repeat combination."""
        with tempfile.TemporaryDirectory() as td:
            exp_a = run_experiment(["waiver-c101"], repeats=2, seed=0, model="scripted",
                                   results_path=f"{td}/a_r.jsonl", trajectories_path=f"{td}/a_t.jsonl")
            exp_b = run_experiment(["waiver-c101"], repeats=2, seed=0, model="scripted",
                                   results_path=f"{td}/b_r.jsonl", trajectories_path=f"{td}/b_t.jsonl")
            ids_a = [r["run_id"] for r in exp_a]
            ids_b = [r["run_id"] for r in exp_b]
            self.assertEqual(len(set(ids_a + ids_b)), len(ids_a) + len(ids_b))
            # No collisions even though human_run_id is identical across experiments
            self.assertEqual([r["human_run_id"] for r in exp_a], [r["human_run_id"] for r in exp_b])
            self.assertNotEqual({r["experiment_id"] for r in exp_a}, {r["experiment_id"] for r in exp_b})
            # run_id is a well-formed UUID
            for rid in ids_a + ids_b:
                uuid.UUID(rid)
            # Unique run_ids also hold across repeat invocations appending to one file
            again = run_experiment(["waiver-c101"], repeats=2, seed=0, model="scripted",
                                   results_path=f"{td}/a_r.jsonl", trajectories_path=f"{td}/a_t.jsonl")
            ids_again = [r["run_id"] for r in again]
            self.assertEqual(len(set(ids_a + ids_again)), len(ids_a) + len(ids_again))

    def test_run_id_consistent_across_both_files(self):
        """Every result row joins to its trajectory on run_id."""
        with tempfile.TemporaryDirectory() as td:
            run_experiment(["waiver-c101", "waiver-c103"], repeats=2, seed=7, model="scripted",
                           results_path=f"{td}/r.jsonl", trajectories_path=f"{td}/t.jsonl")
            with open(f"{td}/r.jsonl", encoding="utf-8") as f:
                row_ids = {json.loads(line)["run_id"] for line in f if line.strip()}
            traj_ids = {t.run_id for t in load_jsonl(f"{td}/t.jsonl")}
            self.assertEqual(row_ids, traj_ids)

    def test_observation_is_JSON_serialized_in_messages(self):
        # We'll check that the observation passed to the model is a JSON string.
        task = get_task("waiver-c101")
        observations = []
        def capture_model(messages, tools, config):
            # We'll capture the last user message (which should be the observation).
            if messages:
                last = messages[-1]
                if last.get("role") == "user" and last.get("content", "").startswith("observation:"):
                    obs_str = last["content"][len("observation:"):]
                    try:
                        parsed = json.loads(obs_str)
                        observations.append(parsed)
                    except json.JSONDecodeError:
                        pass
            # Return a tool call to keep going, then finally answer.
            if not observations:
                return {"tool": "get_customer", "arguments": {"customer_id": "C101"}}
            elif len(observations) == 1:
                return {"tool": "get_account", "arguments": {"customer_id": "C101"}}
            elif len(observations) == 2:
                return {"tool": "get_transactions", "arguments": {"account_id": "A101"}}
            elif len(observations) == 3:
                return {"tool": "search_policy", "arguments": {"query": "waiver"}}
            elif len(observations) == 4:
                return {"tool": "calculate", "arguments": {"op": "avg", "values": [1800, 1200]}}
            else:
                return {"final": "Customer C101 qualifies."}
        traj = run_agent(task["task_id"], task["prompt"], MiniBankEnv(), capture_model, max_steps=8)
        # We should have captured at least one observation.
        self.assertGreaterEqual(len(observations), 1)
        # Each captured observation should be a JSON-serializable object (we already parsed it).
        # Additionally, we can check that the original string was valid JSON.
        for obs in observations:
            self.assertIsInstance(obs, (dict, list, str, int, float, bool)) or obs is None


class TestStepBudget(unittest.TestCase):
    """The interaction budget is per-task (task['max_steps']) and only there.

    The global --max-steps CLI option was a silent no-op (it never reached
    run_agent, which reads task['max_steps']), so it was removed. These tests
    make sure it cannot quietly reappear as a working-looking override.
    """

    def test_run_experiment_has_no_max_steps_parameter(self):
        from minilab.runner import run_experiment
        params = inspect.signature(run_experiment).parameters
        self.assertNotIn("max_steps", params)

    def test_cli_rejects_max_steps_flag(self):
        """A --max-steps override must fail loudly, not be silently ignored."""
        with tempfile.TemporaryDirectory() as td:
            rejected = Path(td) / "r.jsonl"
            proc = subprocess.run(
                [sys.executable, "-m", "minilab.runner", "--tasks", "waiver-c101",
                 "--max-steps", "3", "--results", str(rejected),
                 "--trajectories", f"{td}/t.jsonl"],
                capture_output=True, text=True, timeout=60,
                cwd=str(ROOT),
                env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")},
            )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--max-steps", proc.stderr)
        # No results file should have been produced by a rejected invocation.
        self.assertFalse(rejected.exists())

    def test_cli_help_does_not_mention_max_steps(self):
        proc = subprocess.run(
            [sys.executable, "-m", "minilab.runner", "--help"],
            capture_output=True, text=True, timeout=30, cwd=str(ROOT),
            env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")},
        )
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("--max-steps", proc.stdout)

    def test_per_task_max_steps_preserved(self):
        """The oracle-derived budgets are unchanged, with one slack step."""
        from minilab.tasks import TASKS
        by_id = {t["task_id"]: t["max_steps"] for t in TASKS}
        self.assertEqual(by_id["waiver-c101"], 8)
        self.assertEqual(by_id["waiver-c102"], 7)
        self.assertEqual(by_id["waiver-c103"], 7)
        # Each budget is oracle invocations + 1. c101 oracle: 6 calls + final.
        oracle_calls = len(_ORACLE["waiver-c101"][0])
        self.assertEqual(by_id["waiver-c101"], oracle_calls + 1 + 1)
        for tid in ("waiver-c102", "waiver-c103"):
            calls = len(_ORACLE[tid][0])
            self.assertEqual(by_id[tid], calls + 1 + 1)

    def test_result_row_records_task_budget(self):
        with tempfile.TemporaryDirectory() as td:
            rows = run_experiment(["waiver-c101"], repeats=1, seed=0, model="scripted",
                                  results_path=f"{td}/r.jsonl",
                                  trajectories_path=f"{td}/t.jsonl")
        task = get_task("waiver-c101")
        self.assertEqual(rows[0]["max_steps"], task["max_steps"])
        self.assertEqual(rows[0]["max_steps"], 8)
