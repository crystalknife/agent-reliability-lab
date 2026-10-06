"""Tests for the Groq adapter. All API calls are mocked; no real key required."""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from minilab.agent import run_agent  # noqa: E402
from minilab.env import MiniBankEnv  # noqa: E402
from minilab.evaluation import evaluate  # noqa: E402
from minilab.models.groq import (  # noqa: E402
    DEFAULT_MODEL,
    GROQ_BASE_URL,
    make_groq_model,
)
from minilab.runner import make_model  # noqa: E402
from minilab.tasks import TASKS, get_task  # noqa: E402
from minilab.tools import TOOL_SCHEMAS, dispatch  # noqa: E402


def _tool_response(name, arguments):
    """A Groq/OpenAI-compatible response carrying a single tool call."""
    choice = MagicMock()
    choice.tool_calls = [MagicMock()]
    choice.tool_calls[0].function.name = name
    choice.tool_calls[0].function.arguments = arguments
    choice.message.content = None
    response = MagicMock()
    response.choices = [choice]
    return response


def _text_response(content):
    """A Groq/OpenAI-compatible response carrying only text."""
    choice = MagicMock()
    choice.tool_calls = []
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    return response


class TestGroqAdapter(unittest.TestCase):
    def setUp(self):
        self.key_env = os.environ.pop("GROQ_API_KEY", None)
        os.environ["GROQ_API_KEY"] = "dummy-groq-key"

    def tearDown(self):
        if self.key_env is not None:
            os.environ["GROQ_API_KEY"] = self.key_env
        else:
            os.environ.pop("GROQ_API_KEY", None)

    # --- configuration ---

    def test_client_uses_groq_endpoint_and_key(self):
        with patch("minilab.models.groq.OpenAI") as mock_openai:
            mock_openai.return_value = MagicMock()
            make_groq_model()
        _, kwargs = mock_openai.call_args
        self.assertEqual(kwargs["base_url"], GROQ_BASE_URL)
        self.assertEqual(kwargs["base_url"], "https://api.groq.com/openai/v1")
        self.assertEqual(kwargs["api_key"], "dummy-groq-key")

    def test_default_model(self):
        self.assertEqual(DEFAULT_MODEL, "openai/gpt-oss-120b")
        with patch("minilab.models.groq.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _text_response("done")
            make_groq_model()([], [], {"temperature": 0.0})
        self.assertEqual(
            client.chat.completions.create.call_args.kwargs["model"],
            "openai/gpt-oss-120b",
        )

    def test_explicit_model_selection(self):
        with patch("minilab.models.groq.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _text_response("done")
            make_groq_model("openai/gpt-oss-20b")([], [], {"temperature": 0.0})
        self.assertEqual(
            client.chat.completions.create.call_args.kwargs["model"],
            "openai/gpt-oss-20b",
        )

    def test_missing_api_key_raises(self):
        os.environ.pop("GROQ_API_KEY", None)
        with self.assertRaises(ValueError) as cm:
            make_groq_model()
        self.assertIn("GROQ_API_KEY environment variable not set", str(cm.exception))

    def test_missing_openai_package_raises(self):
        import minilab.models.groq as mod
        original = mod.OpenAI
        mod.OpenAI = None
        try:
            with self.assertRaises(ValueError) as cm:
                make_groq_model()
            self.assertIn("openai package is not installed", str(cm.exception))
        finally:
            mod.OpenAI = original

    # --- tool schema conversion ---

    def test_all_five_minibank_tools_passed(self):
        with patch("minilab.models.groq.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _text_response("done")
            make_groq_model()([{"role": "user", "content": "x"}], TOOL_SCHEMAS, {"temperature": 0.0})
        kwargs = client.chat.completions.create.call_args.kwargs
        sent = kwargs["tools"]
        self.assertEqual(
            [t["function"]["name"] for t in sent],
            ["get_customer", "get_account", "get_transactions", "search_policy", "calculate"],
        )
        # No provider-side tools: only our five local MiniBank tools.
        self.assertEqual(len(sent), 5)
        for t in sent:
            self.assertEqual(t["type"], "function")
            self.assertEqual(t["function"]["parameters"]["type"], "object")

    def test_tool_schema_marks_required_params(self):
        with patch("minilab.models.groq.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _text_response("done")
            make_groq_model()([], TOOL_SCHEMAS, {"temperature": 0.0})
        sent = {t["function"]["name"]: t["function"]["parameters"]
                for t in client.chat.completions.create.call_args.kwargs["tools"]}
        self.assertEqual(sent["get_customer"]["required"], ["customer_id"])
        self.assertEqual(sent["get_transactions"]["required"], ["account_id"])
        self.assertEqual(sent["calculate"]["required"], ["op", "values"])
        # get_account takes no required params
        self.assertEqual(sent["get_account"]["required"], [])
        self.assertEqual(
            sent["get_transactions"]["properties"]["last_n_days"]["type"], "integer",
        )
        # calculate.values must be a properly typed JSON Schema array.
        values_prop = sent["calculate"]["properties"]["values"]
        self.assertEqual(values_prop["type"], "array")
        self.assertEqual(values_prop["items"], {"type": "number"})

    def test_calculate_values_canonical_schema_is_valid_json_schema(self):
        """The single source of truth must not use ad-hoc type strings."""
        schema = {t["name"]: t for t in TOOL_SCHEMAS}["calculate"]
        values = schema["params"]["values"]
        self.assertEqual(values["type"], "array")
        self.assertEqual(values["items"], {"type": "number"})
        self.assertTrue(values["required"])
        # No non-JSON-Schema type strings remain anywhere in the schemas.
        valid = {"string", "integer", "number", "boolean", "array", "object"}
        for t in TOOL_SCHEMAS:
            for pname, spec in t["params"].items():
                self.assertIn(spec["type"], valid,
                              msg=f"{t['name']}.{pname} has invalid type {spec['type']!r}")
                if spec["type"] == "array":
                    self.assertIn("items", spec, msg=f"{t['name']}.{pname} array lacks items")

    def test_calculate_tool_behavior_unchanged(self):
        """The schema fix must not alter calculate's runtime semantics."""
        self.assertEqual(dispatch(MiniBankEnv(), "calculate", {"op": "avg", "values": [1800, 1200]}),
                         {"result": 1500})
        self.assertEqual(dispatch(MiniBankEnv(), "calculate", {"op": "sum", "values": [600]}),
                         {"result": 600})
        # Invalid input still yields the same error payload.
        self.assertEqual(dispatch(MiniBankEnv(), "calculate", {"op": "avg", "values": []}),
                         {"error": "calculate requires a non-empty numeric list 'values'"})
        self.assertEqual(dispatch(MiniBankEnv(), "calculate", {"op": "avg", "values": "1,2"}),
                         {"error": "calculate requires a non-empty numeric list 'values'"})

    def test_temperature_passed_through(self):
        with patch("minilab.models.groq.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _text_response("done")
            make_groq_model()([], [], {"temperature": 0.0})
            self.assertEqual(client.chat.completions.create.call_args.kwargs["temperature"], 0.0)
            client.chat.completions.create.return_value = _text_response("done")
            make_groq_model()([], [], {"temperature": 0.7})
            self.assertEqual(client.chat.completions.create.call_args.kwargs["temperature"], 0.7)

    # --- response parsing ---

    def test_tool_call_becomes_internal_tool_action(self):
        with patch("minilab.models.groq.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _tool_response(
                "get_account", json.dumps({"customer_id": "C101"})
            )
            result = make_groq_model()([], TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertEqual(result, {"tool": "get_account", "arguments": {"customer_id": "C101"}})

    def test_calculate_tool_call_arguments_parsed(self):
        with patch("minilab.models.groq.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _tool_response(
                "calculate", json.dumps({"op": "avg", "values": [1800, 1200]})
            )
            result = make_groq_model()([], TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertEqual(
            result, {"tool": "calculate", "arguments": {"op": "avg", "values": [1800, 1200]}}
        )

    def test_text_response_becomes_final_answer(self):
        with patch("minilab.models.groq.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _text_response(
                "C101 qualifies under WAIVER-01 RULE-A."
            )
            result = make_groq_model()([], TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertEqual(result, {"final": "C101 qualifies under WAIVER-01 RULE-A."})

    def test_null_content_becomes_empty_final(self):
        with patch("minilab.models.groq.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _text_response(None)
            result = make_groq_model()([], TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertEqual(result, {"final": ""})

    def test_malformed_tool_arguments_raise(self):
        with patch("minilab.models.groq.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _tool_response(
                "calculate", "op=avg"
            )
            with self.assertRaises(ValueError) as cm:
                make_groq_model()([], TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertIn("Invalid JSON in tool call arguments", str(cm.exception))

    def test_api_exception_propagates_without_retry(self):
        with patch("minilab.models.groq.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.side_effect = RuntimeError("rate limited")
            with self.assertRaises(RuntimeError) as cm:
                make_groq_model()([], TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertEqual(str(cm.exception), "rate limited")
        self.assertEqual(client.chat.completions.create.call_count, 1)

    def test_api_key_not_leaked_into_request_payload(self):
        with patch("minilab.models.groq.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _text_response("ok")
            make_groq_model()([], TOOL_SCHEMAS, {"temperature": 0.0})
        _, kwargs = client.chat.completions.create.call_args
        self.assertNotIn("dummy-groq-key", json.dumps(kwargs, default=str))

    # --- multi-step loop integration through agent.py ---

    def test_multi_step_tool_loop_executes_locally(self):
        """model -> tool request -> local env execution -> observation -> model."""
        with patch("minilab.models.groq.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.side_effect = [
                _tool_response("get_account", json.dumps({"customer_id": "C101"})),
                _text_response("C101 has two accounts."),
            ]
            model_fn = make_groq_model()
            task = get_task("waiver-c101")
            traj = run_agent(
                task["task_id"], task["prompt"], MiniBankEnv(), model_fn,
                max_steps=task["max_steps"],
            )
        self.assertEqual(traj.termination_reason, "agent_final")
        self.assertEqual(len(traj.steps), 1)
        self.assertEqual(traj.steps[0].action, "get_account")
        # Tool executed locally by the environment, not by the adapter.
        self.assertEqual(len(traj.steps[0].observation), 2)
        self.assertEqual(traj.final_answer, "C101 has two accounts.")

    def test_provider_failure_becomes_model_error_termination(self):
        with patch("minilab.models.groq.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.side_effect = RuntimeError("502 bad gateway")
            task = get_task("waiver-c101")
            traj = run_agent(
                task["task_id"], task["prompt"], MiniBankEnv(), make_groq_model(),
                max_steps=task["max_steps"],
            )
        self.assertEqual(traj.termination_reason, "model_error")
        self.assertFalse(evaluate(task, traj)["passed"])

    # --- runner dispatch ---

    def test_runner_dispatches_groq_syntax(self):
        with patch("minilab.models.groq.OpenAI") as mock_openai:
            mock_openai.return_value = MagicMock()
            self.assertTrue(callable(make_model("groq")))
            _, kwargs = mock_openai.call_args
            self.assertEqual(kwargs["api_key"], "dummy-groq-key")
        with patch("minilab.models.groq.OpenAI") as mock_openai:
            mock_openai.return_value = MagicMock()
            self.assertTrue(callable(make_model("groq:openai/gpt-oss-20b")))


if __name__ == "__main__":
    unittest.main()
