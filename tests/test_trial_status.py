"""Regression tests for trial-level status vs the agent failure taxonomy.

The split under test: a provider/API fault is a property of the trial, not of
the agent. Provider errors must never be charged to the agent, and must never
move the agent reliability number.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from minilab.agent import run_agent  # noqa: E402
from minilab.env import MiniBankEnv  # noqa: E402
from minilab.evaluation import evaluate  # noqa: E402
from minilab.failures import (  # noqa: E402
    FAILURES,
    TRIAL_STATUSES,
    classify,
    classify_trial,
    is_provider_error,
    provider_error_text,
)
from minilab.metrics import summarize  # noqa: E402
from minilab.tasks import get_task  # noqa: E402


def _failing_model(message):
    """A model function that raises, the way a provider fault surfaces."""
    def model_fn(messages, tools, config):
        raise RuntimeError(message)
    return model_fn


def _traj_with_error(task, message):
    """Trajectory terminated by a model_error carrying the provider message."""
    return run_agent(task["task_id"], task["prompt"], MiniBankEnv(),
                     _failing_model(message), max_steps=3)


class TestTrialStatusVocabulary(unittest.TestCase):
    def test_exact_status_set(self):
        self.assertEqual(
            TRIAL_STATUSES,
            ("valid", "provider_error", "environment_error", "evaluator_error"),
        )

    def test_agent_taxonomy_unchanged(self):
        """The agent taxonomy keeps its original members and gains none."""
        for f in ("tool_misuse", "evidence_gap", "policy_misread",
                  "arithmetic_error", "premature_stop", "format_violation"):
            self.assertIn(f, FAILURES)
        for s in TRIAL_STATUSES:
            self.assertNotIn(s, FAILURES)


class TestProviderErrorClassification(unittest.TestCase):
    """Each listed provider fault must classify as provider_error."""

    def setUp(self):
        self.task = get_task("waiver-c101")

    def _assert_provider_error(self, error_repr):
        traj = _traj_with_error(self.task, error_repr)
        self.assertEqual(traj.termination_reason, "model_error")
        self.assertTrue(is_provider_error(traj))
        self.assertEqual(classify_trial(traj), "provider_error")
        # Not an agent failure.
        self.assertEqual(classify(self.task, traj, evaluate(self.task, traj)), "none")

    def test_429_rate_limit(self):
        self._assert_provider_error(
            "Error code: 429 - {'error': {'message': 'Rate limit exceeded: "
            "free-models-per-day'}}"
        )

    def test_401_authentication(self):
        self._assert_provider_error(
            "Error code: 401 - {'error': {'message': 'invalid_api_key'}}"
        )

    def test_403_permission_denied(self):
        self._assert_provider_error(
            "Error code: 403 - {'error': {'message': "
            "'free tier can only be used from within OpenCode'}}"
        )

    def test_404_model_unavailable(self):
        self._assert_provider_error(
            "Error code: 404 - {'error': {'message': 'Model is unavailable.'}}"
        )

    def test_500_internal_server_error(self):
        self._assert_provider_error("Error code: 500 - upstream failed")

    def test_503_service_unavailable(self):
        self._assert_provider_error("Error code: 503 - overloaded")

    def test_connection_failure(self):
        self._assert_provider_error("Connection error.")

    def test_connection_refused(self):
        self._assert_provider_error("connection refused")

    def test_timeout(self):
        self._assert_provider_error("Request timed out.")

    def test_provider_model_error(self):
        self._assert_provider_error("Error code: 400 - Model is unavailable")

    def test_raw_provider_error_preserved(self):
        """The provider's own message must survive verbatim for debugging."""
        raw = ("429 RESOURCE_EXHAUSTED. You exceeded your current quota, "
               "limit: 15, model: gemini-3.5-flash-lite")
        traj = _traj_with_error(self.task, raw)
        text = provider_error_text(traj)
        self.assertIn("429", text)
        self.assertIn("RESOURCE_EXHAUSTED", text)
        self.assertIn("gemini-3.5-flash-lite", text)
        self.assertIn("exceeded your current quota", text)
        # Also on the serialized trajectory, not only the accessor.
        self.assertIn("RESOURCE_EXHAUSTED", json.dumps(traj.to_dict(), default=str))

    def test_bare_model_error_is_provider_error(self):
        """An unrecognised message from the model call is still not agent fault."""
        traj = _traj_with_error(self.task, "ValueError('something odd from the SDK')")
        self.assertEqual(classify_trial(traj), "provider_error")


class TestValidTrialsUnaffected(unittest.TestCase):
    """Valid trials keep their agent failure classification."""

    def setUp(self):
        self.task = get_task("waiver-c101")

    def test_successful_oracle_trial_is_valid_and_none(self):
        from minilab.runner import make_model
        traj = run_agent(self.task["task_id"], self.task["prompt"], MiniBankEnv(),
                         make_model("scripted", self.task["task_id"]),
                         max_steps=self.task["max_steps"])
        verdict = evaluate(self.task, traj)
        self.assertTrue(verdict["passed"])
        self.assertEqual(classify_trial(traj), "valid")
        self.assertEqual(classify(self.task, traj, verdict), "none")

    def test_max_steps_is_valid_premature_stop(self):
        """Hitting the step budget is agent behavior, not a provider fault."""
        model_fn = lambda m, t, c: {"tool": "get_customer", "arguments": {"customer_id": "C1"}}
        traj = run_agent(self.task["task_id"], self.task["prompt"], MiniBankEnv(),
                         model_fn, max_steps=3)
        verdict = evaluate(self.task, traj)
        self.assertEqual(traj.termination_reason, "max_steps")
        self.assertEqual(classify_trial(traj), "valid")
        self.assertEqual(classify(self.task, traj, verdict), "premature_stop")

    def test_tool_error_is_valid_tool_misuse(self):
        """An unknown tool is the agent's mistake, so it stays an agent failure."""
        model_fn = lambda m, t, c: {"tool": "nonexistent_tool", "arguments": {}}
        traj = run_agent(self.task["task_id"], self.task["prompt"], MiniBankEnv(),
                         model_fn, max_steps=3)
        verdict = evaluate(self.task, traj)
        self.assertEqual(traj.termination_reason, "tool_error")
        self.assertEqual(classify_trial(traj), "valid")
        self.assertEqual(classify(self.task, traj, verdict), "tool_misuse")

    def test_invalid_action_is_valid_format_violation(self):
        model_fn = lambda m, t, c: {"unexpected": "shape"}
        traj = run_agent(self.task["task_id"], self.task["prompt"], MiniBankEnv(),
                         model_fn, max_steps=3)
        verdict = evaluate(self.task, traj)
        self.assertEqual(classify_trial(traj), "valid")
        self.assertEqual(classify(self.task, traj, verdict), "format_violation")

    def test_wrong_verdict_is_valid_policy_misread(self):
        task = get_task("waiver-c101")
        final = "C101 does not qualify under WAIVER-01. RULE-B deposits are 0."
        traj = {
            "task_id": task["task_id"],
            "termination_reason": "agent_final",
            "final_answer": final,
            "steps": [
                {"action": "get_account", "arguments": {"customer_id": "C101"},
                 "observation": [{"account_id": "A101", "customer_id": "C101", "balance": 1800},
                                {"account_id": "A102", "customer_id": "C101", "balance": 1200}]},
                {"action": "get_transactions", "arguments": {"account_id": "A101"},
                 "observation": [{"type": "direct_deposit", "amount": 900, "days_ago": 3}]},
                {"action": "search_policy", "arguments": {"query": "waiver"},
                 "observation": [{"policy_id": "WAIVER-01",
                                  "text": "RULE-A average balance >= $1,500; RULE-B deposits >= $500"}]},
                {"action": "calculate", "arguments": {"op": "avg", "values": [1800, 1200]},
                 "observation": {"result": 1500.0}},
            ],
        }
        verdict = evaluate(task, traj)
        self.assertEqual(classify_trial(traj), "valid")
        self.assertEqual(classify(task, traj, verdict), "policy_misread")

    def test_evaluator_error_status(self):
        traj = {"task_id": "waiver-c101", "termination_reason": "agent_final",
                "final_answer": "x", "steps": []}
        self.assertEqual(classify_trial(traj, evaluator_raised=True), "evaluator_error")


class TestCalculateGrounding(unittest.TestCase):
    """A value produced by the deterministic calculate tool is grounded."""

    def setUp(self):
        self.task = get_task("waiver-c101")
        # 1500 appears nowhere in the raw account/transaction data: it exists
        # only as the result of calculate(avg, [1800, 1200]).
        self.steps = [
            {"action": "get_account", "arguments": {"customer_id": "C101"},
             "observation": [{"account_id": "A101", "customer_id": "C101", "balance": 1800},
                            {"account_id": "A102", "customer_id": "C101", "balance": 1200}]},
            {"action": "get_transactions", "arguments": {"account_id": "A101", "last_n_days": 30},
             "observation": [{"type": "direct_deposit", "amount": 900, "days_ago": 5}]},
            {"action": "search_policy", "arguments": {"query": "waiver"},
             "observation": [{"policy_id": "WAIVER-01",
                              "text": "RULE-A average balance >= $1,500; RULE-B deposits >= $500"}]},
            {"action": "calculate", "arguments": {"op": "avg", "values": [1800, 1200]},
             "observation": {"result": 1500.0}},
        ]
        # Sanity: the derived value really is absent from the non-calculate data.
        self.raw = json.dumps([s["observation"] for s in self.steps[:3]])
        self.assertNotIn("1500", self.raw.replace("1,500", ""))

    def _run(self, answer):
        return evaluate(self.task, {"task_id": self.task["task_id"],
                                    "termination_reason": "agent_final",
                                    "final_answer": answer, "steps": self.steps})

    def test_calculate_result_is_grounded(self):
        r = self._run("C101 qualifies under WAIVER-01. RULE-A average balance is 1500.")
        self.assertTrue(r["grounding_ok"], r["reasons"])
        self.assertTrue(r["passed"], r["reasons"])

    def test_grounded_numbers_include_calculate_only_value(self):
        """1500 is grounded by calculate even though no observation contains it."""
        from minilab.evaluation import _observed_numbers
        obs = _observed_numbers(self.steps[:3])  # data tools only, no calculate
        self.assertNotIn(1500.0, obs)
        obs_all = _observed_numbers(self.steps)
        self.assertIn(1500.0, obs_all)

    def test_comma_formatted_derived_value_is_cited(self):
        """$1,500 is the same value as 1500; formatting is not a missing citation."""
        r = self._run("C101 qualifies under WAIVER-01. RULE-A average balance is $1,500.")
        self.assertTrue(r["evidence_match"], r["reasons"])
        self.assertTrue(r["passed"], r["reasons"])

    def test_absent_number_is_still_ungrounded(self):
        """An uncited number that no tool produced must still fail grounding."""
        steps = self.steps[:-1]  # drop the calculate step
        r = evaluate(self.task, {"task_id": self.task["task_id"],
                                 "termination_reason": "agent_final",
                                 "final_answer": "C101 qualifies under WAIVER-01. "
                                                 "RULE-A average balance is 1500.",
                                 "steps": steps})
        self.assertFalse(r["grounding_ok"])
        self.assertTrue(any("ungrounded" in x for x in r["reasons"]))

    def test_non_numeric_evidence_still_substring(self):
        """Policy ids are not numbers; the substring rule is unchanged."""
        r = self._run("C101 qualifies under WAIVER-01. RULE-A average balance is 1500.")
        self.assertNotIn("missing evidence", str(r["reasons"]))
        bad = self._run("C101 qualifies. Average balance is 1500.")
        self.assertTrue(any("missing evidence" in x for x in bad["reasons"]))


class TestMetricsExcludesProviderErrors(unittest.TestCase):
    """Agent reliability must be computed over valid trials only."""

    def _rows(self):
        def row(status, passed, failure):
            return {"task_id": "waiver-c101", "difficulty": "medium",
                    "trial_status": status, "passed": passed, "failure": failure}
        return [
            row("provider_error", False, "none"),
            row("provider_error", False, "none"),
            row("valid", True, "none"),
            row("valid", True, "none"),
            row("valid", False, "evidence_gap"),
        ]

    def test_total_and_valid_reported(self):
        t = summarize(self._rows())["trials"]
        self.assertEqual(t["total"], 5)
        self.assertEqual(t["valid"], 3)
        self.assertEqual(t["provider_errors"], 2)

    def test_agent_reliability_over_valid_only(self):
        s = summarize(self._rows())
        self.assertEqual(s["agent_reliability"]["n"], 3)
        self.assertAlmostEqual(s["agent_reliability"]["success_rate"], 2 / 3)

    def test_provider_errors_do_not_increment_failure_counts(self):
        s = summarize(self._rows())
        self.assertEqual(s["failures"].get("evidence_gap"), 1)
        self.assertNotIn("tool_misuse", s["failures"])

    def test_failure_counts_exclude_provider_errors_entirely(self):
        """Even if a stale row claims a failure, provider trials are not counted."""
        rows = [{"task_id": "t", "difficulty": "d", "trial_status": "provider_error",
                 "passed": False, "failure": "tool_misuse"}]
        s = summarize(rows)
        self.assertEqual(s["failures"], {})
        self.assertEqual(s["trials"]["valid"], 0)

    def test_by_status_counts(self):
        self.assertEqual(summarize(self._rows())["trials"]["by_status"],
                         {"provider_error": 2, "valid": 3})

    def test_rows_without_status_default_to_valid(self):
        """Results written before statuses existed still aggregate."""
        rows = [{"task_id": "t", "difficulty": "d", "passed": True, "failure": "none"}]
        s = summarize(rows)
        self.assertEqual(s["trials"]["valid"], 1)
        self.assertEqual(s["failures"], {"none": 1})


if __name__ == "__main__":
    unittest.main()