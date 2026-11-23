"""Hosted OpenAI-compatible adapters against REAL SDK response objects.

Regression tests for three adapter defects found in review (OpenAI, Groq, OpenRouter, and
OmniRoute, which delegates to the OpenAI adapter):
  B1  tool calls were read from `choice.tool_calls`; real SDK responses carry them on
      `choice.message.tool_calls`, so every real response raised AttributeError.
  B2  the agent's raw history (dict-content tool calls, "observation:" user turns) was sent
      as request messages; OpenAI-compatible servers reject that (HTTP 400).
  B3  array tool parameters lost their `items`; OpenAI rejects such schemas (HTTP 400).

Responses are built with the SDK's own validator (tests/sdk_chat.py), never MagicMock, so a
test cannot invent a field the SDK lacks. Only the HTTP client is replaced; no network call.
"""

import contextlib
import json
import os
import unittest
from unittest import mock

from openai.types.chat import ChatCompletion

from minilab.agent import run_agent
from minilab.env import MiniBankEnv
from minilab.evaluation import evaluate
from minilab.failures import classify_trial
from minilab.models.groq import make_groq_model
from minilab.models.local import to_api_messages
from minilab.models.omniroute import make_omniroute_model
from minilab.models.openai import make_openai_model
from minilab.models.openrouter import make_openrouter_model
from minilab.runner import _ORACLE
from minilab.tasks import get_task
from minilab.tools import TOOL_SCHEMAS
from sdk_chat import chat_completion, text_response, tool_call_response

# name -> (factory, patched OpenAI client class, API-key variable or None)
ADAPTERS = {
    "openai": (lambda: make_openai_model("gpt-test"), "minilab.models.openai.OpenAI", "OPENAI_API_KEY"),
    "groq": (make_groq_model, "minilab.models.groq.OpenAI", "GROQ_API_KEY"),
    "openrouter": (make_openrouter_model, "minilab.models.openrouter.OpenAI", "OPENROUTER_API_KEY"),
    "omniroute": (make_omniroute_model, "minilab.models.openai.OpenAI", None),   # delegates to openai.py
}
MESSAGES = [{"role": "system", "content": "s"}, {"role": "user", "content": "Look up C101."}]
CONFIG = {"temperature": 0.0}


@contextlib.contextmanager
def adapter(name, *responses):
    """The real adapter with its HTTP client replaced; yields (model_fn, create_mock)."""
    factory, client_path, key_env = ADAPTERS[name]
    env = {key_env: "test-key"} if key_env else {}
    with mock.patch.dict(os.environ, env), mock.patch(client_path) as client_cls:
        create = client_cls.return_value.chat.completions.create
        create.side_effect = list(responses)
        yield factory(), create


class TestResponseHandling(unittest.TestCase):
    def test_1_single_tool_call_is_extracted(self):
        for name in ADAPTERS:
            with self.subTest(adapter=name), adapter(name, tool_call_response("get_account", {"customer_id": "C101"})) as (fn, _):
                self.assertEqual(fn(MESSAGES, TOOL_SCHEMAS, CONFIG),
                                 {"tool": "get_account", "arguments": {"customer_id": "C101"}})

    def test_2_text_response_is_the_final_answer(self):
        for name in ADAPTERS:
            with self.subTest(adapter=name), adapter(name, text_response("C101 qualifies.")) as (fn, _):
                self.assertEqual(fn(MESSAGES, TOOL_SCHEMAS, CONFIG), {"final": "C101 qualifies."})

    def test_3_empty_response_is_an_empty_final_answer(self):
        for name in ADAPTERS:
            for content in (None, ""):
                with self.subTest(adapter=name, content=content), adapter(name, chat_completion(content=content)) as (fn, _):
                    self.assertEqual(fn(MESSAGES, TOOL_SCHEMAS, CONFIG), {"final": ""})

    def test_4_malformed_arguments_raise_a_structured_error(self):
        for name in ADAPTERS:
            with self.subTest(adapter=name), adapter(name, tool_call_response("get_account", "{not json")) as (fn, _):
                with self.assertRaises(ValueError) as ctx:
                    fn(MESSAGES, TOOL_SCHEMAS, CONFIG)
                self.assertIn("Invalid JSON in tool call arguments for tool get_account", str(ctx.exception))
                self.assertIsInstance(ctx.exception.__cause__, json.JSONDecodeError)

    def test_5_truncated_arguments_raise(self):
        # What an OpenAI-compatible server returns when generation stops inside a tool call
        # (observed on llama-server b11435): HTTP 200, finish_reason=length, partial arguments.
        for name in ADAPTERS:
            truncated = tool_call_response("get_account", '{"customer_id":', finish_reason="length")
            with self.subTest(adapter=name), adapter(name, truncated) as (fn, _):
                with self.assertRaises(ValueError) as ctx:
                    fn(MESSAGES, TOOL_SCHEMAS, CONFIG)
                self.assertIn("Invalid JSON in tool call arguments", str(ctx.exception))

    def test_6_multiple_tool_calls_keep_the_first_only(self):
        # Current behaviour, pinned on purpose: these adapters return the first call only
        # (the local adapter returns all of them as {"calls": [...]}). Not changed by this fix.
        two = chat_completion(tool_calls=[("get_customer", {"customer_id": "C101"}),
                                          ("get_account", {"customer_id": "C101"})])
        for name in ADAPTERS:
            with self.subTest(adapter=name), adapter(name, two) as (fn, _):
                self.assertEqual(fn(MESSAGES, TOOL_SCHEMAS, CONFIG),
                                 {"tool": "get_customer", "arguments": {"customer_id": "C101"}})


class TestRequests(unittest.TestCase):
    HISTORY = MESSAGES + [
        {"role": "assistant", "content": {"tool": "get_customer", "arguments": {"customer_id": "C101"}}},
        {"role": "user", "content": 'observation: {"customer_id": "C101"}'},
    ]

    def test_7_follow_up_requests_use_api_message_shapes(self):
        for name in ADAPTERS:
            with self.subTest(adapter=name), adapter(name, text_response("done")) as (fn, create):
                fn(list(self.HISTORY), TOOL_SCHEMAS, CONFIG)
                sent = create.call_args.kwargs["messages"]
                self.assertEqual(sent, to_api_messages(self.HISTORY))
                self.assertFalse(any(isinstance(m.get("content"), dict) for m in sent), "dict content is rejected (400)")
                call = next(m for m in sent if m["role"] == "assistant")
                tool = next(m for m in sent if m["role"] == "tool")
                self.assertEqual(call["tool_calls"][0]["function"],
                                 {"name": "get_customer", "arguments": json.dumps({"customer_id": "C101"})})
                self.assertEqual(tool["tool_call_id"], call["tool_calls"][0]["id"])
                self.assertEqual(json.loads(tool["content"]), {"customer_id": "C101"})
                self.assertFalse(any(str(m.get("content", "")).startswith("observation:") for m in sent))

    def test_7b_the_callers_history_is_not_mutated(self):
        for name in ADAPTERS:
            history = json.loads(json.dumps(self.HISTORY))
            with self.subTest(adapter=name), adapter(name, text_response("done")) as (fn, _):
                fn(history, TOOL_SCHEMAS, CONFIG)
                self.assertEqual(history, self.HISTORY)

    def test_8_array_parameters_keep_their_items(self):
        internal = {(t["name"], p): s for t in TOOL_SCHEMAS for p, s in t["params"].items() if s.get("type") == "array"}
        self.assertIn(("calculate", "values"), internal)
        for name in ADAPTERS:
            with self.subTest(adapter=name), adapter(name, text_response("x")) as (fn, create):
                fn(MESSAGES, TOOL_SCHEMAS, CONFIG)
                sent = {t["function"]["name"]: t["function"]["parameters"]["properties"] for t in create.call_args.kwargs["tools"]}
                for (tool, param), spec in internal.items():
                    self.assertEqual(sent[tool][param].get("items"), spec["items"], f"{tool}.{param}")
                for props in sent.values():
                    for pname, prop in props.items():
                        if prop.get("type") == "array":
                            self.assertIn("items", prop, f"array parameter {pname} without items")


class TestSdkShapeGuard(unittest.TestCase):
    def test_9_tool_calls_exist_only_on_the_message(self):
        choice = tool_call_response("get_account", {"customer_id": "C101"}).choices[0]
        self.assertNotIn("tool_calls", type(choice).model_fields)
        with self.assertRaises(AttributeError):
            choice.tool_calls                       # the access that broke every adapter (B1)
        self.assertEqual(choice.message.tool_calls[0].function.name, "get_account")

    def test_9b_responses_are_real_sdk_objects(self):
        # Guards the tests themselves: a MagicMock response would accept any attribute.
        response = text_response("x")
        self.assertIsInstance(response, ChatCompletion)
        with self.assertRaises(AttributeError):
            response.choices[0].not_a_field


class TestEndToEnd(unittest.TestCase):
    def test_10_run_agent_completes_a_valid_trial_through_each_adapter(self):
        task = get_task("waiver-c101")
        calls, final = _ORACLE["waiver-c101"]
        responses = [tool_call_response(tool, args) for tool, args in calls] + [text_response(final)]
        for name in ADAPTERS:
            with self.subTest(adapter=name), adapter(name, *responses) as (fn, create):
                traj = run_agent(task["task_id"], task["prompt"], MiniBankEnv(), fn, max_steps=task["max_steps"])
                self.assertEqual(traj.termination_reason, "agent_final")
                self.assertEqual([s.action for s in traj.steps], [tool for tool, _ in calls])
                self.assertTrue(all("error" not in (s.observation if isinstance(s.observation, dict) else {})
                                    for s in traj.steps), "every tool ran in the local environment")
                self.assertEqual(traj.final_answer, final)
                self.assertEqual(classify_trial(traj), "valid")
                self.assertTrue(evaluate(task, traj)["passed"])
                self.assertEqual(create.call_count, len(calls) + 1)
                for i, c in enumerate(create.call_args_list):
                    sent = c.kwargs["messages"]
                    self.assertFalse(any(isinstance(m.get("content"), dict) for m in sent), f"request {i}")
                    self.assertEqual(sum(m["role"] == "tool" for m in sent), i, f"request {i} carries {i} observations")


if __name__ == "__main__":
    unittest.main()
