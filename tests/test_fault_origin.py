"""Fault origin: planted controls for every exception boundary.

fault_origin() is a NON-SCORING annotation with three separate parts:
origin (what was observed), attribution (established / unattributed) and
basis (typed record / message text). These tests plant one known cause per
case and check that the annotation claims no more than the boundary shows.
They also pin that the trial-status vocabulary, the agent failure taxonomy
and the reliability denominators are exactly what they were.

All trajectories are synthetic; the "SDK" exceptions are local classes whose
module name imitates an SDK. No provider is contacted.
"""

import tempfile
import unittest
from unittest import mock

from minilab.agent import run_agent
from minilab.env import MiniBankEnv
from minilab.evaluation import evaluate
from minilab.failures import (
    FAILURES, FAULT_ATTRIBUTIONS, FAULT_BASES, FAULT_ORIGINS, TRIAL_STATUSES,
    classify, classify_trial, fault_origin,
)
from minilab.metrics import summarize
from minilab.runner import run_experiment
from minilab.tasks import get_task

TASK = get_task("waiver-c101")


def exc_class(name, module, bases=(Exception,), **attrs):
    return type(name, bases, {"__module__": module, **attrs})


def scripted(responses):
    it = iter(responses)

    def fn(messages, tools, config):
        out = next(it)
        if isinstance(out, BaseException):
            raise out
        return out

    return fn


def trajectory(responses, env=None):
    return run_agent(TASK["task_id"], TASK["prompt"], env or MiniBankEnv(), scripted(responses), max_steps=6)


def annotate(traj, **kw):
    o = fault_origin(traj, **kw)
    return o["origin"], o["attribution"], o["basis"]


def status_and_failure(traj):
    return classify_trial(traj), classify(TASK, traj, evaluate(TASK, traj))


TYPED = "typed_boundary_and_class"


class TestVocabularies(unittest.TestCase):
    def test_scoring_vocabularies_are_unchanged(self):
        self.assertEqual(TRIAL_STATUSES, ("valid", "provider_error", "environment_error", "evaluator_error"))
        self.assertEqual(FAILURES, ("none", "tool_misuse", "evidence_gap", "policy_misread",
                                    "arithmetic_error", "premature_stop", "format_violation"))

    def test_annotation_vocabularies(self):
        self.assertEqual(FAULT_ATTRIBUTIONS, ("established", "unattributed", "none"))
        self.assertEqual(FAULT_BASES, ("typed_boundary_and_class", "typed_boundary_only", "text_heuristic", "none"))
        self.assertEqual(len(FAULT_ORIGINS), len(set(FAULT_ORIGINS)))
        self.assertFalse((set(FAULT_ORIGINS) & set(FAILURES)) - {"none"})
        self.assertFalse(set(FAULT_ORIGINS) & set(TRIAL_STATUSES))


class TestModelCallBoundary(unittest.TestCase):
    """Controls 1-3: an SDK class name alone never establishes provider fault."""

    def test_control_external_service_error_status(self):
        # Planted: the service answered 429 / 503 (status carried by the exception object).
        for name, status in (("RateLimitError", 429), ("InternalServerError", 503), ("APIStatusError", 500)):
            traj = trajectory([exc_class(name, "openai", status_code=status)("x")])
            o = fault_origin(traj)
            self.assertEqual((o["origin"], o["attribution"], o["basis"], o["http_status"]),
                             ("model_service_error", "established", TYPED, status), name)
            self.assertEqual(classify_trial(traj), "provider_error")
        # google-genai carries the status in `.code`
        traj = trajectory([exc_class("ServerError", "google.genai.errors", code=503)("x")])
        self.assertEqual(annotate(traj)[0], "model_service_error")

    def test_control_request_rejected_is_not_blamed_on_the_provider(self):
        # Planted: 401 (bad/missing key = local configuration), 400 (a request the
        # adapter built), 404 (model name). All SDK classes; none proves provider fault.
        for name, status in (("AuthenticationError", 401), ("BadRequestError", 400),
                             ("NotFoundError", 404), ("PermissionDeniedError", 403),
                             ("UnprocessableEntityError", 422)):
            traj = trajectory([exc_class(name, "openai", status_code=status)("x")])
            self.assertEqual(annotate(traj), ("request_rejected", "unattributed", TYPED), name)
            self.assertEqual(classify_trial(traj), "provider_error")       # scoring label unchanged

    def test_control_transport_failure_is_unattributed_whoever_raised_it(self):
        api_conn = exc_class("APIConnectionError", "openai")
        cases = [ConnectionRefusedError("x"), TimeoutError("x"), ConnectionResetError("x"),
                 api_conn("x"), exc_class("APITimeoutError", "openai", bases=(api_conn,))("x"),
                 exc_class("ReadTimeout", "httpx")("x"), exc_class("ConnectError", "httpcore")("x")]
        for exc in cases:
            self.assertEqual(annotate(trajectory([exc])), ("transport", "unattributed", TYPED),
                             type(exc).__name__)

    def test_control_builtin_timeout_raised_by_local_adapter_logic(self):
        # Planted: the ADAPTER itself raises TimeoutError (a local watchdog).
        # It is indistinguishable from a network timeout at this boundary, so
        # it must not be reported as an established external failure.
        def adapter(messages, tools, config):
            raise TimeoutError("local watchdog expired")

        traj = run_agent(TASK["task_id"], TASK["prompt"], MiniBankEnv(), adapter, max_steps=3)
        o = fault_origin(traj)
        self.assertEqual((o["origin"], o["attribution"]), ("transport", "unattributed"))

    def test_control_sdk_validation_or_configuration_exception(self):
        # Planted: SDK-side exceptions with no HTTP status (missing key, schema validation).
        for exc in (exc_class("OpenAIError", "openai")("api_key must be set"),
                    exc_class("ValidationError", "pydantic_core._pydantic_core")("1 validation error"),
                    exc_class("ClientError", "google.genai.errors")("bad config")):
            self.assertEqual(annotate(trajectory([exc])), ("sdk_unattributed", "unattributed", TYPED),
                             type(exc).__name__)

    def test_control_local_adapter_exception(self):
        for exc in (KeyError("choices"), AttributeError("NoneType has no attribute tool_calls"),
                    RuntimeError("adapter bug"), ZeroDivisionError()):
            traj = trajectory([exc])
            self.assertEqual(annotate(traj), ("model_call_local", "unattributed", TYPED), type(exc).__name__)
            # Scoring deliberately unchanged: still excluded under the old label.
            self.assertEqual(status_and_failure(traj), ("provider_error", "none"))

    def test_control_malformed_model_output_parsed_inside_the_adapter(self):
        # Planted: the adapter fails to parse tool-call arguments the MODEL
        # produced and raises ValueError (what local.py/openai.py do). At the
        # harness boundary this looks exactly like an adapter bug.
        traj = trajectory([ValueError("could not parse tool arguments as JSON")])
        self.assertEqual(annotate(traj), ("model_call_local", "unattributed", TYPED))

    def test_control_local_exception_with_status_like_attribute(self):
        # D2 planted: a purely local exception carrying `.code = 503` / `.status_code = 429`.
        for exc in (exc_class("LocalErr", "minilab.models.local", code=503)("x"),
                    exc_class("AdapterErr", "my_adapter", status_code=429)("x")):
            o = fault_origin(trajectory([exc]))
            self.assertEqual((o["origin"], o["attribution"], o["http_status"]),
                             ("model_call_local", "unattributed", None), type(exc).__name__)
            self.assertEqual(classify_trial(trajectory([exc])), "provider_error")   # scoring unchanged

    def test_provider_like_text_never_overrides_the_typed_record(self):
        traj = trajectory([ValueError("rate limit 429 timeout unauthorized error code: 503")])
        self.assertEqual(annotate(traj)[0], "model_call_local")

    def test_status_is_read_from_the_object_not_from_text(self):
        traj = trajectory([exc_class("APIError", "openai")("Error code: 503 - overloaded")])
        o = fault_origin(traj)
        self.assertIsNone(o["http_status"])
        self.assertEqual(o["origin"], "sdk_unattributed")


class TestOutputAndToolBoundaries(unittest.TestCase):
    def test_control_malformed_model_output_reaching_the_harness(self):
        for bad in ({"nonsense": 1}, {"calls": []}, "just text", {"tool": "get_account", "arguments": "x"}):
            traj = trajectory([bad])
            self.assertEqual(traj.termination_reason, "invalid_action")
            self.assertEqual(annotate(traj), ("model_output_format", "established", "typed_boundary_only"))
            self.assertEqual(status_and_failure(traj), ("valid", "format_violation"))   # agent failure, as before

    def test_control_unknown_tool(self):
        traj = trajectory([{"tool": "delete_account", "arguments": {}}])
        self.assertEqual(annotate(traj), ("agent_unknown_tool", "established", TYPED))
        self.assertEqual(status_and_failure(traj), ("valid", "tool_misuse"))

    def test_control_invalid_tool_arguments(self):
        # Planted: arguments that violate the tool's declared schema and make the tool raise.
        traj = trajectory([{"tool": "get_transactions", "arguments": {"account_id": "A101", "last_n_days": "abc"}}])
        self.assertEqual(traj.termination_reason, "tool_error")
        o = fault_origin(traj)
        self.assertEqual((o["origin"], o["attribution"], o["exception_type"]),
                         ("agent_invalid_arguments", "established", "TypeError"))
        self.assertEqual(status_and_failure(traj), ("valid", "tool_misuse"))

    def test_control_environment_or_tool_implementation_exception(self):
        # Planted: schema-valid arguments, and the environment itself is broken.
        class BrokenEnv(MiniBankEnv):
            def get_account(self, customer_id=None, account_id=None):
                raise KeyError("accounts")

        traj = trajectory([{"tool": "get_account", "arguments": {"customer_id": "C101"}}], env=BrokenEnv())
        o = fault_origin(traj)
        self.assertEqual((o["origin"], o["attribution"]), ("tool_or_environment", "unattributed"))
        # KNOWN LIMIT, pinned: scoring still charges this to the agent.
        self.assertEqual(status_and_failure(traj), ("valid", "tool_misuse"))

    def test_schema_valid_arguments_do_not_prove_an_environment_defect(self):
        # A semantically bad but schema-valid value (negative window) with an
        # environment that rejects it: still only "tool_or_environment".
        class StrictEnv(MiniBankEnv):
            def get_transactions(self, account_id, last_n_days=None):
                if last_n_days is not None and last_n_days < 0:
                    raise ValueError("negative window")
                return super().get_transactions(account_id, last_n_days)

        traj = trajectory([{"tool": "get_transactions", "arguments": {"account_id": "A101", "last_n_days": -5}}],
                          env=StrictEnv())
        self.assertEqual(annotate(traj)[:2], ("tool_or_environment", "unattributed"))

    def test_typed_tool_fault_with_provider_like_text(self):
        class SlowEnv(MiniBankEnv):
            def get_account(self, customer_id=None, account_id=None):
                raise RuntimeError("connection timed out")

        traj = trajectory([{"tool": "get_account", "arguments": {"customer_id": "C101"}}], env=SlowEnv())
        self.assertEqual(annotate(traj)[0], "tool_or_environment")       # tools are local: not a provider fault
        self.assertEqual(classify_trial(traj), "provider_error")         # legacy text rule, unchanged; they disagree visibly

    def test_error_dict_results_are_not_faults(self):
        for responses in ([{"final": "C101 qualifies"}],
                          [{"tool": "get_customer", "arguments": {"customer_id": "C999"}}, {"final": "x"}],
                          [{"tool": "calculate", "arguments": {"op": "sum", "values": []}}, {"final": "x"}]):
            self.assertEqual(annotate(trajectory(responses)), ("none", "none", "none"))


class TestEvaluatorBoundary(unittest.TestCase):
    def test_control_evaluator_exception_with_its_type(self):
        traj = trajectory([{"final": "x"}])
        o = fault_origin(traj, evaluator_raised=True, evaluator_exception=ZeroDivisionError())
        self.assertEqual((o["origin"], o["attribution"], o["basis"], o["boundary"], o["exception_type"]),
                         ("evaluator", "established", TYPED, "evaluator", "ZeroDivisionError"))
        self.assertEqual(classify_trial(traj, evaluator_raised=True), "evaluator_error")

    def test_flag_without_exception_is_boundary_only(self):
        o = fault_origin(trajectory([{"final": "x"}]), evaluator_raised=True)
        self.assertEqual((o["origin"], o["basis"], o["exception_type"]), ("evaluator", "typed_boundary_only", None))

    def test_runner_records_the_evaluator_exception_type(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch("minilab.runner.evaluate", side_effect=KeyError("required_evidence")):
                rows = run_experiment(["waiver-c101"], 1, 0, "scripted", 0.0, f"{td}/r.jsonl", f"{td}/t.jsonl")
        r = rows[0]
        self.assertEqual((r["trial_status"], r["passed"]), ("evaluator_error", False))
        self.assertEqual((r["fault_origin"], r["fault_attribution"], r["fault_basis"], r["fault_boundary"],
                          r["fault_exception_type"]),
                         ("evaluator", "established", TYPED, "evaluator", "KeyError"))


class TestWithoutTypedRecords(unittest.TestCase):
    """Control 9: historical trajectories and untyped paths stay lower-confidence."""

    @staticmethod
    def legacy(term, error, action=None):
        return {"task_id": "t", "termination_reason": term, "final_answer": None,
                "steps": [{"step": 1, "action": action or ("model_error" if term == "model_error" else "get_account"),
                           "arguments": {}, "observation": {"error": error}}]}

    def test_text_rule_is_reported_as_a_suspicion(self):
        o = fault_origin(self.legacy("model_error", "model error: RateLimitError('Error code: 429')"))
        self.assertEqual((o["origin"], o["attribution"], o["basis"], o["boundary"]),
                         ("provider_suspected", "unattributed", "text_heuristic", None))

    def test_untyped_model_error_without_a_provider_pattern(self):
        self.assertEqual(annotate(self.legacy("model_error", "model error: ValueError('bad json')")),
                         ("model_call_unattributed", "unattributed", "text_heuristic"))

    def test_untyped_tool_error_and_invalid_action_carry_no_annotation(self):
        self.assertEqual(annotate(self.legacy("tool_error", "unknown tool: x")), ("none", "none", "none"))
        self.assertEqual(annotate(self.legacy("invalid_action", "invalid model output: 1", "invalid_action")),
                         ("none", "none", "none"))

    def test_no_untyped_case_is_ever_established(self):
        for term, err in (("model_error", "model error: APIConnectionError('Connection error.')"),
                          ("tool_error", "tool error: timed out"), ("model_error", "x")):
            self.assertNotEqual(fault_origin(self.legacy(term, err))["attribution"], "established")

    def test_historical_status_classification_is_unchanged(self):
        self.assertEqual(classify_trial(self.legacy("model_error", "model error: ValueError('bad json')")),
                         "provider_error")
        self.assertEqual(classify_trial(self.legacy("tool_error", "unknown tool: x")), "valid")

    def test_old_fault_record_shape_is_still_read(self):
        # Records written earlier on 2026-11-16 lack bases/status/tool_known.
        traj = self.legacy("tool_error", "tool error: KeyError('x')")
        traj["steps"][0]["observation"]["fault"] = {"boundary": "tool_dispatch", "exception_type": "KeyError",
                                                    "exception_module": "builtins"}
        self.assertEqual(annotate(traj)[:2], ("tool_execution_unattributed", "unattributed"))


class TestReliabilityDenominators(unittest.TestCase):
    def test_only_valid_trials_are_scored(self):
        def row(status, passed, failure="none"):
            return {"task_id": "t", "difficulty": "easy", "trial_status": status, "passed": passed,
                    "failure": failure}

        rows = ([row("valid", True)] * 2 + [row("valid", False, "tool_misuse")]
                + [row("provider_error", False)] * 5 + [row("environment_error", False)] * 4
                + [row("evaluator_error", False)] * 3)
        s = summarize(rows)
        self.assertEqual(s["agent_reliability"]["n"], 3)
        self.assertAlmostEqual(s["agent_reliability"]["success_rate"], 2 / 3)
        self.assertEqual(s["failures"], {"none": 2, "tool_misuse": 1})
        self.assertEqual(s["trials"]["by_status"],
                         {"valid": 3, "provider_error": 5, "environment_error": 4, "evaluator_error": 3})

    def test_rows_gain_only_additive_annotation_fields(self):
        with tempfile.TemporaryDirectory() as td:
            rows = run_experiment(["waiver-c101", "calc-total-c101"], 1, 0, "scripted", 0.0,
                                  f"{td}/r.jsonl", f"{td}/t.jsonl")
        for r in rows:
            self.assertEqual((r["fault_origin"], r["fault_attribution"], r["fault_basis"],
                              r["fault_boundary"], r["fault_exception_type"]),
                             ("none", "none", "none", None, None))
            self.assertEqual((r["trial_status"], r["passed"], r["failure"]), ("valid", True, "none"))


if __name__ == "__main__":
    unittest.main()
