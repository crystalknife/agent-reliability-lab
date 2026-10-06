"""Tests for the local llama-server adapter. No real network calls.

Mocks use the REAL OpenAI SDK response shape (tool calls on
`choice.message`, not `choice`): this is the shape llama-server returns and
the reason the adapter parses `message.tool_calls`.
"""

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
from minilab.failures import classify, classify_trial  # noqa: E402
from minilab.models.local import (  # noqa: E402
    API_KEY_ENV,
    DEFAULT_MODEL,
    LOCAL_BASE_URL,
    MODEL_VERSION,
    PLACEHOLDER_API_KEY,
    make_local_model,
    to_api_messages,
)
from minilab.runner import make_model  # noqa: E402
from minilab.tasks import get_task  # noqa: E402
from minilab.tools import TOOL_SCHEMAS, build_system_prompt  # noqa: E402


def _tool_response(name, args):
    """Mocked SDK response with a tool call at the REAL location."""
    response = MagicMock()
    choice = response.choices[0]
    choice.message.tool_calls = [MagicMock()]
    call = choice.message.tool_calls[0]
    call.function.name = name
    call.function.arguments = json.dumps(args)
    choice.message.content = None
    return response


def _text_response(text):
    response = MagicMock()
    choice = response.choices[0]
    choice.message.tool_calls = None
    choice.message.content = text
    return response


class TestLocalAdapter(unittest.TestCase):
    def setUp(self):
        self.key_env = os.environ.pop(API_KEY_ENV, None)
        self.openai_key = os.environ.pop("OPENAI_API_KEY", None)
        self.messages = [
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": "Look up customer C101."},
        ]

    def tearDown(self):
        if self.key_env is not None:
            os.environ[API_KEY_ENV] = self.key_env
        if self.openai_key is not None:
            os.environ["OPENAI_API_KEY"] = self.openai_key

    def test_endpoint_and_identity(self):
        self.assertEqual(LOCAL_BASE_URL, "http://127.0.0.1:8081/v1")
        self.assertEqual(DEFAULT_MODEL, "qwen3-4b")
        self.assertEqual(MODEL_VERSION, "llama.cpp-b11435")

    def test_client_targets_local_server_with_placeholder_key(self):
        with patch("minilab.models.local.OpenAI") as mock_openai:
            make_local_model()
        _, kwargs = mock_openai.call_args
        self.assertEqual(kwargs["base_url"], "http://127.0.0.1:8081/v1")
        self.assertEqual(kwargs["api_key"], PLACEHOLDER_API_KEY)

    def test_never_reads_openai_key(self):
        """OPENAI_API_KEY must be irrelevant, even when set to junk."""
        os.environ["OPENAI_API_KEY"] = "sk-should-never-be-used"
        with patch("minilab.models.local.OpenAI") as mock_openai:
            make_local_model()
        _, kwargs = mock_openai.call_args
        self.assertEqual(kwargs["api_key"], PLACEHOLDER_API_KEY)

    def test_tool_call_parsed_from_message(self):
        """The real SDK shape: tool calls live on choice.message."""
        with patch("minilab.models.local.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _tool_response(
                "get_customer", {"customer_id": "C101"})
            out = make_local_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertEqual(out, {"tool": "get_customer",
                               "arguments": {"customer_id": "C101"}})

    def test_text_response_becomes_final(self):
        with patch("minilab.models.local.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _text_response("done")
            out = make_local_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertEqual(out, {"final": "done"})

    def test_malformed_arguments_raise(self):
        response = MagicMock()
        call = response.choices[0].message.tool_calls[0]
        call.function.name = "get_account"
        call.function.arguments = "{not json"
        with patch("minilab.models.local.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = response
            with self.assertRaises(ValueError):
                make_local_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})

    def test_multi_turn_tool_loop(self):
        with patch("minilab.models.local.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.side_effect = [
                _tool_response("get_customer", {"customer_id": "C101"}),
                _text_response("C101 exists."),
            ]
            task = get_task("waiver-c101")
            traj = run_agent(
                task["task_id"], task["prompt"], MiniBankEnv(), make_local_model(),
                max_steps=task["max_steps"],
            )
        self.assertEqual(traj.termination_reason, "agent_final")
        self.assertEqual(traj.steps[0].action, "get_customer")
        self.assertEqual(traj.final_answer, "C101 exists.")

    def test_first_turn_history_passes_through(self):
        """System + user strings are sent untouched."""
        with patch("minilab.models.local.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _text_response("ok")
            make_local_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        sent = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(sent, self.messages)

    def test_assistant_tool_call_serialized(self):
        """B: internal dict-content becomes a proper tool_calls message."""
        history = self.messages + [
            {"role": "assistant",
             "content": {"tool": "search_policy", "arguments": {"query": "waiver"}}},
        ]
        out = to_api_messages(history)
        self.assertEqual(out[:2], self.messages)
        msg = out[2]
        self.assertEqual(msg["role"], "assistant")
        self.assertIsNone(msg["content"])
        (call,) = msg["tool_calls"]
        self.assertEqual(call["type"], "function")
        self.assertEqual(call["function"]["name"], "search_policy")
        self.assertEqual(json.loads(call["function"]["arguments"]), {"query": "waiver"})
        self.assertTrue(call["id"])

    def test_tool_result_serialized(self):
        """C: 'observation: ...' becomes a tool-role message linked by id."""
        history = self.messages + [
            {"role": "assistant",
             "content": {"tool": "search_policy", "arguments": {"query": "waiver"}}},
            {"role": "user", "content": 'observation: [{"policy_id": "WAIVER-01"}]'},
        ]
        out = to_api_messages(history)
        tool_msg = out[3]
        self.assertEqual(tool_msg["role"], "tool")
        self.assertEqual(tool_msg["tool_call_id"], out[2]["tool_calls"][0]["id"])
        self.assertEqual(json.loads(tool_msg["content"]), [{"policy_id": "WAIVER-01"}])

    def test_ids_stable_across_turns(self):
        """Generated ids are positional, so rebuilt histories stay consistent."""
        history = self.messages + [
            {"role": "assistant", "content": {"tool": "a", "arguments": {}}},
            {"role": "user", "content": "observation: {}"},
            {"role": "assistant", "content": {"tool": "b", "arguments": {}}},
            {"role": "user", "content": "observation: {}"},
        ]
        out = to_api_messages(history)
        self.assertEqual(out[2]["tool_calls"][0]["id"], out[3]["tool_call_id"])
        self.assertEqual(out[4]["tool_calls"][0]["id"], out[5]["tool_call_id"])
        self.assertNotEqual(out[2]["tool_calls"][0]["id"], out[4]["tool_calls"][0]["id"])

    def test_multi_turn_sends_valid_history(self):
        """D: the exact failing shape — dict content must never reach the server."""
        with patch("minilab.models.local.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.side_effect = [
                _tool_response("search_policy", {"query": "waiver"}),
                _tool_response("get_customer", {"customer_id": "C101"}),
                _text_response("C101 exists."),
            ]
            task = get_task("waiver-c101")
            traj = run_agent(
                task["task_id"], task["prompt"], MiniBankEnv(), make_local_model(),
                max_steps=task["max_steps"],
            )
        self.assertEqual(traj.termination_reason, "agent_final")
        for call in client.chat.completions.create.call_args_list[1:]:
            for m in call.kwargs["messages"]:
                self.assertTrue(
                    m.get("content") is None or isinstance(m.get("content"), (str, list)),
                    m)
            assistants = [m for m in call.kwargs["messages"] if m["role"] == "assistant"
                          and "tool_calls" in m]
            tools = [m for m in call.kwargs["messages"] if m["role"] == "tool"]
            self.assertTrue(assistants and tools)
            self.assertEqual({m["tool_call_id"] for m in tools},
                             {c["id"] for a in assistants for c in a["tool_calls"]})

    def test_server_down_becomes_provider_error_not_agent_failure(self):
        """Connection refused must classify as provider_error, never tool_misuse."""
        with patch("minilab.models.local.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.side_effect = ConnectionError("connection refused")
            task = get_task("waiver-c101")
            traj = run_agent(
                task["task_id"], task["prompt"], MiniBankEnv(), make_local_model(),
                max_steps=task["max_steps"],
            )
        self.assertEqual(traj.termination_reason, "model_error")
        self.assertEqual(client.chat.completions.create.call_count, 1)
        self.assertEqual(classify_trial(traj), "provider_error")
        self.assertEqual(classify(task, traj, {"passed": False}), "none")

    def test_runner_dispatch_and_provenance(self):
        """local:/local:qwen3-4b dispatch, explicit model id, full provenance."""
        import tempfile
        from minilab.runner import run_experiment
        with patch("minilab.models.local.OpenAI") as mock_openai:
            client = mock_openai.return_value
            client.chat.completions.create.return_value = _text_response("ok")
            make_model("local")(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
            self.assertEqual(
                client.chat.completions.create.call_args.kwargs["model"], "qwen3-4b")
            client.chat.completions.create.reset_mock()
            make_model("local:qwen3-4b")(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
            self.assertEqual(
                client.chat.completions.create.call_args.kwargs["model"], "qwen3-4b")
            with tempfile.TemporaryDirectory() as td:
                rows = run_experiment(
                    ["retrieval-bal-a103"], repeats=1, seed=0, model="local:qwen3-4b",
                    results_path=f"{td}/r.jsonl", trajectories_path=f"{td}/t.jsonl")
                traj = json.loads(open(f"{td}/t.jsonl", encoding="utf-8").readline())
        self.assertEqual(traj["provider"], "local")
        self.assertEqual(traj["model"], "local:qwen3-4b")
        self.assertEqual(traj["model_version"], "llama.cpp-b11435")


if __name__ == "__main__":
    unittest.main()
