"""Execution-mode handling: sequential default preserved, batch dispatches all.

Inference-free: scripted model functions and mocked adapter responses only.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from minilab.agent import run_agent  # noqa: E402
from minilab.env import MiniBankEnv  # noqa: E402
from minilab.evaluation import evaluate  # noqa: E402
from minilab.failures import classify  # noqa: E402
from minilab.models.local import make_local_model  # noqa: E402
from minilab.runner import _provenance_counts, run_experiment  # noqa: E402
from minilab.tasks import get_task  # noqa: E402
from minilab.tools import TOOL_SCHEMAS  # noqa: E402

MESSAGES = [{"role": "user", "content": "Hi."}]


def _sdk_response(calls):
    """Mocked OpenAI SDK response with N tool calls on choice.message."""
    response = MagicMock()
    response.choices[0].message.tool_calls = []
    for name, args in calls:
        call = MagicMock()
        call.function.name = name
        call.function.arguments = json.dumps(args)
        response.choices[0].message.tool_calls.append(call)
    response.choices[0].message.content = None
    return response


def _scripted_plan(responses):
    state = {"i": 0}

    def model_fn(messages, tools, config):
        out = responses[state["i"]]
        state["i"] += 1
        return out
    return model_fn


def _fixed_meta():
    return {"experiment_id": "t", "run_id": "r", "timestamp": 0,
            "model": "m", "provider": "p", "temperature": 0.0, "seed": 0}


class TestAdapterCallList(unittest.TestCase):
    def test_single_call_keeps_historical_shape(self):
        with patch("minilab.models.local.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _sdk_response(
                [("get_customer", {"customer_id": "C101"})])
            out = make_local_model()(MESSAGES, TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertEqual(out, {"tool": "get_customer",
                               "arguments": {"customer_id": "C101"}})

    def test_multi_call_returns_full_list(self):
        with patch("minilab.models.local.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _sdk_response([
                ("get_customer", {"customer_id": "C101"}),
                ("get_account", {"customer_id": "C101"}),
            ])
            out = make_local_model()(MESSAGES, TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertEqual(out, {"calls": [
            {"tool": "get_customer", "arguments": {"customer_id": "C101"}},
            {"tool": "get_account", "arguments": {"customer_id": "C101"}},
        ]})


class TestBatchMode(unittest.TestCase):
    def test_unknown_mode_raises(self):
        with self.assertRaises(ValueError):
            run_agent("waiver-c101", "p", MiniBankEnv(),
                      _scripted_plan([{"final": "x"}]), exec_mode="parallel")

    def test_zero_calls_is_invalid_action(self):
        traj = run_agent("waiver-c101", "p", MiniBankEnv(),
                         _scripted_plan([{"calls": []}, {"final": "x"}]),
                         exec_mode="batch")
        self.assertEqual(traj.termination_reason, "invalid_action")

    def test_sequential_keeps_first_only(self):
        calls = {"calls": [
            {"tool": "get_customer", "arguments": {"customer_id": "C101"}},
            {"tool": "get_account", "arguments": {"customer_id": "C101"}},
        ]}
        seq = run_agent("waiver-c101", "p", MiniBankEnv(),
                        _scripted_plan([calls, {"final": "done"}]),
                        exec_mode="sequential", meta=_fixed_meta())
        single = run_agent("waiver-c101", "p", MiniBankEnv(),
                           _scripted_plan([
                               {"tool": "get_customer", "arguments": {"customer_id": "C101"}},
                               {"final": "done"}]),
                           exec_mode="sequential", meta=_fixed_meta())
        self.assertEqual([s.action for s in seq.steps], ["get_customer"])
        # Same executed behavior; only the recorded raw model output differs,
        # which is correct (raw_history must preserve what was emitted).
        for attr in ("steps", "final_answer", "termination_reason"):
            self.assertEqual(getattr(seq, attr), getattr(single, attr))

    def test_batch_dispatches_in_order_with_matching(self):
        calls = {"calls": [
            {"tool": "get_customer", "arguments": {"customer_id": "C101"}},
            {"tool": "get_account", "arguments": {"customer_id": "C101"}},
        ]}
        traj = run_agent("waiver-c101", "p", MiniBankEnv(),
                         _scripted_plan([calls, {"final": "done"}]),
                         exec_mode="batch")
        self.assertEqual(traj.termination_reason, "agent_final")
        self.assertEqual([s.action for s in traj.steps],
                         ["get_customer", "get_account"])
        self.assertEqual(traj.steps[0].observation,
                         {"customer_id": "C101", "name": "Alice Rivera"})
        self.assertEqual(traj.steps[1].observation[0]["account_id"], "A101")
        self.assertEqual(traj.steps[1].observation[1]["account_id"], "A102")

    def test_exception_aborts_turn_preserving_completed(self):
        calls = {"calls": [
            {"tool": "get_customer", "arguments": {"customer_id": "C101"}},
            {"tool": "nope_missing", "arguments": {}},
            {"tool": "get_account", "arguments": {"customer_id": "C101"}},
        ]}
        traj = run_agent("waiver-c101", "p", MiniBankEnv(),
                         _scripted_plan([calls, {"final": "done"}]),
                         exec_mode="batch")
        self.assertEqual(traj.termination_reason, "tool_error")
        self.assertEqual([s.action for s in traj.steps],
                         ["get_customer", "nope_missing"])
        self.assertNotIn("error", traj.steps[0].observation)
        self.assertIn("error", traj.steps[1].observation)

    def test_error_dict_observation_is_non_terminal(self):
        calls = {"calls": [
            {"tool": "calculate", "arguments": {"op": "bogus", "values": [1]}},
            {"tool": "get_customer", "arguments": {"customer_id": "C101"}},
        ]}
        traj = run_agent("waiver-c101", "p", MiniBankEnv(),
                         _scripted_plan([calls, {"final": "done"}]),
                         exec_mode="batch")
        self.assertEqual(traj.termination_reason, "agent_final")
        self.assertEqual([s.action for s in traj.steps],
                         ["calculate", "get_customer"])
        self.assertIn("error", traj.steps[0].observation)

    def test_budget_counts_model_requests_not_tool_calls(self):
        calls = {"calls": [
            {"tool": "get_customer", "arguments": {"customer_id": "C101"}},
            {"tool": "get_account", "arguments": {"customer_id": "C101"}},
        ]}
        traj = run_agent("waiver-c101", "p", MiniBankEnv(),
                         _scripted_plan([calls, {"final": "done"}]),
                         max_steps=1, exec_mode="batch")
        # The batch turn consumes the single model-request budget, then the
        # final has no budget left: premature stop, with both calls recorded.
        self.assertEqual(traj.termination_reason, "max_steps")
        self.assertEqual([s.action for s in traj.steps],
                         ["get_customer", "get_account"])
        model_requests, tool_calls = _provenance_counts(traj)
        self.assertEqual(model_requests, 1)
        self.assertEqual(tool_calls, 2)


class TestExecProvenance(unittest.TestCase):
    def test_sequential_rows_carry_counters(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            rows = run_experiment(["retrieval-bal-a103"], repeats=1, seed=0,
                                  model="scripted",
                                  results_path=f"{td}/r.jsonl",
                                  trajectories_path=f"{td}/t.jsonl",
                                  framework="minilab", exec_mode="sequential")
        row = rows[0]
        self.assertEqual(row["exec_mode"], "sequential")
        # 1 model request (get_account) + 1 final = 2 responses; 1 tool call.
        self.assertEqual(row["model_requests"], 2)
        self.assertEqual(row["tool_calls"], 1)
        self.assertTrue(row["passed"])

    def test_exec_batch_rejected_for_langchain(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                run_experiment(["waiver-c101"], repeats=1, model="scripted",
                               results_path=f"{td}/r.jsonl",
                               trajectories_path=f"{td}/t.jsonl",
                               framework="langchain", exec_mode="batch")

    def test_unknown_exec_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                run_experiment(["waiver-c101"], repeats=1, model="scripted",
                               results_path=f"{td}/r.jsonl",
                               trajectories_path=f"{td}/t.jsonl",
                               exec_mode="sideways")

    def test_batch_counters_from_agent(self):
        task = get_task("calc-total-c101")
        calls = {"calls": [
            {"tool": "get_account", "arguments": {"customer_id": "C101"}},
            {"tool": "calculate", "arguments": {"op": "sum", "values": [1800, 1200]}},
        ]}
        traj = run_agent(task["task_id"], task["prompt"], MiniBankEnv(),
                         _scripted_plan([calls, {"final": "Combined: 3000."}]),
                         max_steps=task["max_steps"], exec_mode="batch")
        model_requests, tool_calls = _provenance_counts(traj)
        self.assertEqual(model_requests, 2)
        self.assertEqual(tool_calls, 2)
        self.assertTrue(evaluate(task, traj)["passed"])
        self.assertEqual(classify(task, traj, evaluate(task, traj)), "none")


if __name__ == "__main__":
    unittest.main()
