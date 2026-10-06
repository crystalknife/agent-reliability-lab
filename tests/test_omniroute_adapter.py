"""Tests for the OmniRoute adapter. No real network calls; the client is mocked."""

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
from minilab.models.openai import make_openai_model  # noqa: E402
from minilab.models.omniroute import (  # noqa: E402
    API_KEY_ENV,
    DEFAULT_MODEL,
    OMNIROUTE_BASE_URL,
    PLACEHOLDER_API_KEY,
    make_omniroute_model,
)
from minilab.runner import make_model  # noqa: E402
from minilab.tasks import get_task  # noqa: E402
from minilab.tools import TOOL_SCHEMAS, build_system_prompt  # noqa: E402

FIVE = ["get_customer", "get_account", "get_transactions", "search_policy", "calculate"]


def _tool_response(name, args):
    response = MagicMock()
    choice = response.choices[0]
    choice.tool_calls = [MagicMock()]
    call = choice.tool_calls[0]
    call.function.name = name
    call.function.arguments = json.dumps(args)
    choice.message.content = None
    return response


def _text_response(text):
    response = MagicMock()
    choice = response.choices[0]
    choice.tool_calls = None
    choice.message.content = text
    return response


def _sent_tools(client):
    return {t["function"]["name"]: t["function"]["parameters"]
            for t in client.chat.completions.create.call_args.kwargs["tools"]}


class TestOmniRouteAdapter(unittest.TestCase):
    def setUp(self):
        self.key_env = os.environ.pop(API_KEY_ENV, None)
        self.openai_key = os.environ.get("OPENAI_API_KEY")
        self.messages = [
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": "Does C101 qualify?"},
        ]

    def tearDown(self):
        if self.key_env is not None:
            os.environ[API_KEY_ENV] = self.key_env
        else:
            os.environ.pop(API_KEY_ENV, None)
        if self.openai_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = self.openai_key

    # --- base URL and client configuration ---

    def test_base_url_constant(self):
        self.assertEqual(OMNIROUTE_BASE_URL, "http://127.0.0.1:20128/v1")

    def test_client_uses_omniroute_base_url(self):
        with patch("minilab.models.openai.OpenAI") as mock_openai:
            make_omniroute_model()
        _, kwargs = mock_openai.call_args
        self.assertEqual(kwargs["base_url"], "http://127.0.0.1:20128/v1")

    def test_placeholder_key_when_env_unset(self):
        """The local server does not authenticate; the SDK still needs a value."""
        os.environ.pop(API_KEY_ENV, None)
        with patch("minilab.models.openai.OpenAI") as mock_openai:
            make_omniroute_model()
        _, kwargs = mock_openai.call_args
        self.assertEqual(kwargs["api_key"], PLACEHOLDER_API_KEY)

    def test_env_key_used_when_set(self):
        os.environ[API_KEY_ENV] = "omniroute-key-abc"
        with patch("minilab.models.openai.OpenAI") as mock_openai:
            make_omniroute_model()
        _, kwargs = mock_openai.call_args
        self.assertEqual(kwargs["api_key"], "omniroute-key-abc")

    def test_does_not_require_openai_api_key(self):
        """OmniRoute must not fall back to the OpenAI credential."""
        os.environ.pop("OPENAI_API_KEY", None)
        with patch("minilab.models.openai.OpenAI") as mock_openai:
            make_omniroute_model()
        _, kwargs = mock_openai.call_args
        self.assertEqual(kwargs["api_key"], PLACEHOLDER_API_KEY)

    # --- model identity ---

    def test_default_model(self):
        self.assertEqual(DEFAULT_MODEL, "oc/deepseek-v4-flash-free")

    def test_default_model_sent_verbatim(self):
        with patch("minilab.models.openai.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _text_response("ok")
            make_omniroute_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertEqual(
            client.chat.completions.create.call_args.kwargs["model"],
            "oc/deepseek-v4-flash-free",
        )

    def test_explicit_model_sent_verbatim(self):
        with patch("minilab.models.openai.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _text_response("ok")
            make_omniroute_model("oc/qwen3-coder-free")(
                self.messages, TOOL_SCHEMAS, {"temperature": 0.0}
            )
        self.assertEqual(
            client.chat.completions.create.call_args.kwargs["model"], "oc/qwen3-coder-free"
        )

    def test_no_auto_model_is_ever_substituted(self):
        """The configured model must not be rewritten to an auto/* id."""
        with patch("minilab.models.openai.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _text_response("ok")
            model_fn = make_omniroute_model("oc/deepseek-v4-flash-free")
            for _ in range(3):
                model_fn(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        for call in client.chat.completions.create.call_args_list:
            sent = call.kwargs["model"]
            self.assertNotIn("auto", sent)
            self.assertEqual(sent, "oc/deepseek-v4-flash-free")

    # --- request construction ---

    def test_request_construction(self):
        with patch("minilab.models.openai.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _text_response("ok")
            make_omniroute_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        kwargs = client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["messages"], self.messages)
        self.assertEqual(kwargs["tool_choice"], "auto")
        self.assertEqual(kwargs["temperature"], 0.0)

    def test_temperature_passthrough(self):
        with patch("minilab.models.openai.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _text_response("ok")
            model_fn = make_omniroute_model()
            model_fn(self.messages, TOOL_SCHEMAS, {"temperature": 0.7})
            self.assertEqual(
                client.chat.completions.create.call_args.kwargs["temperature"], 0.7
            )

    # --- tool schemas pass through ---

    def test_all_five_tools_passed_through(self):
        with patch("minilab.models.openai.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _text_response("ok")
            make_omniroute_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertEqual(list(_sent_tools(client)), FIVE)

    def test_required_params_preserved(self):
        with patch("minilab.models.openai.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _text_response("ok")
            make_omniroute_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        sent = _sent_tools(client)
        self.assertEqual(sent["get_customer"]["required"], ["customer_id"])
        self.assertEqual(sent["get_account"]["required"], [])
        self.assertEqual(sent["get_transactions"]["required"], ["account_id"])
        self.assertEqual(sent["calculate"]["required"], ["op", "values"])

    def test_tool_types_preserved(self):
        with patch("minilab.models.openai.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _text_response("ok")
            make_omniroute_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        sent = _sent_tools(client)
        self.assertEqual(
            sent["get_transactions"]["properties"]["last_n_days"]["type"], "integer"
        )
        # Matches the OpenAI adapter's existing behavior for arrays.
        self.assertEqual(
            sent["calculate"]["properties"]["values"]["type"], "array"
        )

    # --- response parsing ---

    def test_tool_call_parsed(self):
        with patch("minilab.models.openai.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _tool_response(
                "get_account", {"customer_id": "C101"}
            )
            out = make_omniroute_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertEqual(out, {"tool": "get_account", "arguments": {"customer_id": "C101"}})

    def test_numeric_array_arguments_preserved(self):
        with patch("minilab.models.openai.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _tool_response(
                "calculate", {"op": "avg", "values": [1800, 1200]}
            )
            out = make_omniroute_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertEqual(out["arguments"]["values"], [1800, 1200])
        self.assertIsInstance(out["arguments"]["values"], list)

    def test_text_parsed_as_final(self):
        with patch("minilab.models.openai.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _text_response("The customer qualifies.")
            out = make_omniroute_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertEqual(out, {"final": "The customer qualifies."})

    def test_malformed_tool_arguments_raise(self):
        response = MagicMock()
        call = response.choices[0].tool_calls[0]
        call.function.name = "get_account"
        call.function.arguments = "{not json"
        with patch("minilab.models.openai.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = response
            with self.assertRaises(ValueError) as cm:
                make_omniroute_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertIn("Invalid JSON in tool call arguments", str(cm.exception))

    def test_error_propagates_without_retry(self):
        with patch("minilab.models.openai.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.side_effect = ConnectionError("connection refused")
            with self.assertRaises(ConnectionError):
                make_omniroute_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertEqual(client.chat.completions.create.call_count, 1)

    # --- integration through the agent loop ---

    def test_multi_turn_tool_loop(self):
        with patch("minilab.models.openai.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.side_effect = [
                _tool_response("get_customer", {"customer_id": "C101"}),
                _text_response("C101 exists."),
            ]
            task = get_task("waiver-c101")
            traj = run_agent(
                task["task_id"], task["prompt"], MiniBankEnv(), make_omniroute_model(),
                max_steps=task["max_steps"],
            )
        self.assertEqual(traj.termination_reason, "agent_final")
        self.assertEqual(len(traj.steps), 1)
        self.assertEqual(traj.steps[0].action, "get_customer")
        self.assertEqual(traj.final_answer, "C101 exists.")
        # Second request carried the observation back through the standard history.
        second = client.chat.completions.create.call_args_list[1].kwargs["messages"]
        self.assertTrue(any("observation:" in str(m["content"]) for m in second))

    def test_provider_failure_becomes_model_error(self):
        with patch("minilab.models.openai.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.side_effect = ConnectionError("connection refused")
            task = get_task("waiver-c101")
            traj = run_agent(
                task["task_id"], task["prompt"], MiniBankEnv(), make_omniroute_model(),
                max_steps=task["max_steps"],
            )
        self.assertEqual(traj.termination_reason, "model_error")

    # --- runner dispatch ---

    def test_runner_dispatch_omniroute(self):
        with patch("minilab.models.openai.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _text_response("ok")
            self.assertTrue(callable(make_model("omniroute")))
            _, kwargs = mock_openai.call_args
            self.assertEqual(kwargs["base_url"], OMNIROUTE_BASE_URL)
            client.chat.completions.create.reset_mock()
            make_model("omniroute")(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertEqual(
            client.chat.completions.create.call_args.kwargs["model"], DEFAULT_MODEL
        )

    def test_runner_dispatch_omniroute_explicit_model(self):
        with patch("minilab.models.openai.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _text_response("ok")
            make_model("omniroute:oc/deepseek-v4-flash-free")(
                self.messages, TOOL_SCHEMAS, {"temperature": 0.0}
            )
        self.assertEqual(
            client.chat.completions.create.call_args.kwargs["model"],
            "oc/deepseek-v4-flash-free",
        )


class TestOpenAIBehaviorUnchanged(unittest.TestCase):
    """Refactoring make_openai_model must not change default OpenAI behavior."""

    def setUp(self):
        self.key_env = os.environ.pop("OPENAI_API_KEY", None)
        os.environ["OPENAI_API_KEY"] = "dummy-openai-key"

    def tearDown(self):
        if self.key_env is not None:
            os.environ["OPENAI_API_KEY"] = self.key_env
        else:
            os.environ.pop("OPENAI_API_KEY", None)

    def test_no_base_url_by_default(self):
        with patch("minilab.models.openai.OpenAI") as mock_openai:
            make_openai_model("gpt-4o")
        _, kwargs = mock_openai.call_args
        self.assertNotIn("base_url", kwargs)
        self.assertEqual(kwargs["api_key"], "dummy-openai-key")

    def test_missing_openai_key_still_raises(self):
        os.environ.pop("OPENAI_API_KEY", None)
        with self.assertRaises(ValueError) as cm:
            make_openai_model("gpt-4o")
        self.assertIn("OPENAI_API_KEY environment variable not set", str(cm.exception))


if __name__ == "__main__":
    unittest.main()