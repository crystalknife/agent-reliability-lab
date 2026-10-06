"""Tests for the Gemini native adapter. The API is mocked; no real key required."""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from google.genai import types as gtypes  # noqa: E402

from minilab.agent import run_agent  # noqa: E402
from minilab.env import MiniBankEnv  # noqa: E402
from minilab.evaluation import evaluate  # noqa: E402
from minilab.models.gemini import (  # noqa: E402
    DEFAULT_MODEL,
    make_gemini_model,
    _normalize_args,
)
from minilab.runner import make_model  # noqa: E402
from minilab.tasks import get_task  # noqa: E402
from minilab.tools import TOOL_SCHEMAS, build_system_prompt  # noqa: E402

FIVE = ["get_customer", "get_account", "get_transactions", "search_policy", "calculate"]


def _text_part(text):
    return gtypes.Part.from_text(text=text)


def _fc_part(name, args):
    return gtypes.Part.from_function_call(name=name, args=args)


def _response(parts):
    content = gtypes.Content(role="model", parts=parts)
    candidate = MagicMock()
    candidate.content = content
    response = MagicMock()
    response.candidates = [candidate]
    return response


def _text_response(text):
    return _response([_text_part(text)])


def _signed_part(name, args, signature):
    """A model function-call Part carrying opaque thought metadata."""
    return gtypes.Part(
        function_call=gtypes.FunctionCall(name=name, args=args),
        thought_signature=signature,
    )


def _tool_response(name, args):
    return _response([_fc_part(name, args)])


def _signed_tool_response(name, args, signature):
    return _response([_signed_part(name, args, signature)])


def _run(task_id, model_fn, steps=None):
    task = get_task(task_id)
    return run_agent(
        task["task_id"], task["prompt"], MiniBankEnv(), model_fn,
        max_steps=task["max_steps"],
    )


class TestGeminiAdapter(unittest.TestCase):
    def setUp(self):
        self.key_env = os.environ.pop("GEMINI_API_KEY", None)
        os.environ["GEMINI_API_KEY"] = "dummy-gemini-key"
        self.messages = [
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": "Does C101 qualify?"},
        ]

    def tearDown(self):
        if self.key_env is not None:
            os.environ["GEMINI_API_KEY"] = self.key_env
        else:
            os.environ.pop("GEMINI_API_KEY", None)

    # --- client / API configuration ---

    def test_client_constructed_with_api_key(self):
        with patch("minilab.models.gemini.genai") as mock_genai:
            make_gemini_model()
        mock_genai.Client.assert_called_once()
        _, kwargs = mock_genai.Client.call_args
        self.assertEqual(kwargs["api_key"], "dummy-gemini-key")

    def test_no_vertex_or_aggregator_config(self):
        """Native Gemini API only: no base_url / OpenRouter / OpenAI layer."""
        with patch("minilab.models.gemini.genai") as mock_genai:
            make_gemini_model()
        _, kwargs = mock_genai.Client.call_args
        self.assertNotIn("base_url", kwargs)
        self.assertNotIn("http_options", kwargs)

    def test_api_key_not_leaked_into_request(self):
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.return_value = _text_response("ok")
            make_gemini_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        _, kwargs = client.models.generate_content.call_args
        self.assertNotIn("dummy-gemini-key", json.dumps(kwargs, default=str))

    # --- model selection ---

    def test_default_model(self):
        self.assertEqual(DEFAULT_MODEL, "gemini-2.5-flash")
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.return_value = _text_response("ok")
            make_gemini_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertEqual(
            client.models.generate_content.call_args.kwargs["model"], "gemini-2.5-flash"
        )

    def test_explicit_model_override(self):
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.return_value = _text_response("ok")
            make_gemini_model("gemini-2.5-pro")(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertEqual(
            client.models.generate_content.call_args.kwargs["model"], "gemini-2.5-pro"
        )

    def test_missing_api_key_raises(self):
        os.environ.pop("GEMINI_API_KEY", None)
        with self.assertRaises(ValueError) as cm:
            make_gemini_model()
        self.assertIn("GEMINI_API_KEY environment variable not set", str(cm.exception))

    def test_missing_sdk_raises(self):
        import minilab.models.gemini as mod
        original = mod.genai
        mod.genai = None
        try:
            with self.assertRaises(ValueError) as cm:
                make_gemini_model()
            self.assertIn("google-genai package is not installed", str(cm.exception))
        finally:
            mod.genai = original

    # --- tool declaration conversion ---

    def _sent_tool(self, client):
        cfg = client.models.generate_content.call_args.kwargs["config"]
        return cfg.tools[0].function_declarations

    def test_all_five_tools_declared(self):
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.return_value = _text_response("ok")
            make_gemini_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertEqual([d.name for d in self._sent_tool(client)], FIVE)

    def test_required_params_preserved(self):
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.return_value = _text_response("ok")
            make_gemini_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        by_name = {d.name: d.parameters_json_schema for d in self._sent_tool(client)}
        self.assertEqual(by_name["get_customer"]["required"], ["customer_id"])
        self.assertEqual(by_name["get_account"]["required"], [])
        self.assertEqual(by_name["get_transactions"]["required"], ["account_id"])
        self.assertEqual(by_name["search_policy"]["required"], ["query"])
        self.assertEqual(by_name["calculate"]["required"], ["op", "values"])

    def test_calculate_values_array_items_schema(self):
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.return_value = _text_response("ok")
            make_gemini_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        by_name = {d.name: d.parameters_json_schema for d in self._sent_tool(client)}
        values = by_name["calculate"]["properties"]["values"]
        self.assertEqual(values["type"], "array")
        self.assertEqual(values["items"], {"type": "number"})
        # integer typing preserved too
        tx = by_name["get_transactions"]["properties"]["last_n_days"]
        self.assertEqual(tx["type"], "integer")

    def test_descriptions_preserved(self):
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.return_value = _text_response("ok")
            make_gemini_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        by_name = {d.name: d.description for d in self._sent_tool(client)}
        self.assertEqual(by_name["calculate"],
                         "Sandboxed arithmetic over an explicit list of numbers. No expressions.")

    def test_no_hosted_tools_exposed(self):
        """No Google Search grounding / code execution / URL context / remote tools."""
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.return_value = _text_response("ok")
            make_gemini_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        cfg = client.models.generate_content.call_args.kwargs["config"]
        for tool in cfg.tools:
            self.assertIsNone(tool.google_search)
            self.assertIsNone(tool.google_search_retrieval)
            self.assertIsNone(tool.code_execution)
            self.assertIsNone(tool.url_context)
        self.assertTrue(cfg.automatic_function_calling.disable)

    # --- request shape ---

    def test_system_prompt_becomes_system_instruction(self):
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.return_value = _text_response("ok")
            make_gemini_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        cfg = client.models.generate_content.call_args.kwargs["config"]
        self.assertEqual(cfg.system_instruction, build_system_prompt())

    def test_temperature_passthrough(self):
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.return_value = _text_response("ok")
            make_gemini_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
            self.assertEqual(client.models.generate_content.call_args.kwargs["config"].temperature, 0.0)
            client.models.generate_content.return_value = _text_response("ok")
            make_gemini_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.7})
            self.assertEqual(client.models.generate_content.call_args.kwargs["config"].temperature, 0.7)

    def test_task_prompt_sent_as_user_content(self):
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.return_value = _text_response("ok")
            make_gemini_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        contents = client.models.generate_content.call_args.kwargs["contents"]
        self.assertEqual(contents[0].role, "user")
        self.assertEqual(contents[0].parts[0].text, "Does C101 qualify?")

    # --- response parsing ---

    def test_function_call_becomes_tool_action(self):
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.return_value = _tool_response(
                "get_account", {"customer_id": "C101"}
            )
            out = make_gemini_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertEqual(out, {"tool": "get_account", "arguments": {"customer_id": "C101"}})

    def test_calculate_values_preserved_as_number_array(self):
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.return_value = _tool_response(
                "calculate", {"op": "avg", "values": [1800, 1200]}
            )
            out = make_gemini_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertEqual(out["tool"], "calculate")
        self.assertEqual(out["arguments"]["values"], [1800, 1200])
        self.assertTrue(all(isinstance(v, (int, float)) for v in out["arguments"]["values"]))
        self.assertNotIsInstance(out["arguments"]["values"], str)
        # JSON round-trips as an array
        self.assertIsInstance(json.loads(json.dumps(out["arguments"]))["values"], list)

    def test_text_becomes_final_answer(self):
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.return_value = _text_response(
                "C101 qualifies under WAIVER-01 RULE-A."
            )
            out = make_gemini_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertEqual(out, {"final": "C101 qualifies under WAIVER-01 RULE-A."})

    def test_multiple_function_calls_are_deterministic_and_recorded(self):
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.return_value = _response([
                _fc_part("get_customer", {"customer_id": "C101"}),
                _fc_part("get_account", {"customer_id": "C101"}),
            ])
            out = make_gemini_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        # First call is the executed action; extras are recorded, never dropped silently.
        self.assertEqual(out["tool"], "get_customer")
        self.assertEqual(out["arguments"], {"customer_id": "C101"})
        self.assertEqual(out["deferred_tool_calls"], [{"tool": "get_account", "arguments": {"customer_id": "C101"}}])

    def test_malformed_arguments_raise(self):
        # The SDK's own Part type validates args as a dict, so a malformed
        # string can only reach the adapter from a non-conforming provider
        # response. Bypass pydantic with a duck-typed part.
        part = MagicMock()
        part.function_call.name = "calculate"
        part.function_call.args = "op=avg"
        part.text = None
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            candidate = MagicMock()
            candidate.content.parts = [part]
            response = MagicMock()
            response.candidates = [candidate]
            client.models.generate_content.return_value = response
            with self.assertRaises(ValueError) as cm:
                make_gemini_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertIn("Invalid JSON in function call arguments", str(cm.exception))

    def test_non_dict_decoded_arguments_raise(self):
        part = MagicMock()
        part.function_call.name = "get_account"
        part.function_call.args = "[1, 2]"
        part.text = None
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            candidate = MagicMock()
            candidate.content.parts = [part]
            response = MagicMock()
            response.candidates = [candidate]
            client.models.generate_content.return_value = response
            with self.assertRaises(ValueError) as cm:
                make_gemini_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertIn("must decode to an object", str(cm.exception))

    def test_normalize_args_passthrough(self):
        self.assertEqual(_normalize_args(None), {})
        self.assertEqual(_normalize_args({"a": 1}), {"a": 1})
        self.assertEqual(_normalize_args('{"a": 1}'), {"a": 1})

    def test_api_exception_propagates_without_retry(self):
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.side_effect = RuntimeError("429 RESOURCE_EXHAUSTED")
            with self.assertRaises(RuntimeError) as cm:
                make_gemini_model()(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertEqual(str(cm.exception), "429 RESOURCE_EXHAUSTED")
        self.assertEqual(client.models.generate_content.call_count, 1)

    # --- multi-turn loop through agent.py ---

    def test_multi_turn_tool_loop(self):
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.side_effect = [
                _tool_response("get_account", {"customer_id": "C101"}),
                _text_response("C101 has two accounts."),
            ]
            task = get_task("waiver-c101")
            traj = run_agent(
                task["task_id"], task["prompt"], MiniBankEnv(), make_gemini_model(),
                max_steps=task["max_steps"],
            )
        self.assertEqual(traj.termination_reason, "agent_final")
        self.assertEqual(len(traj.steps), 1)
        self.assertEqual(traj.steps[0].action, "get_account")
        # Executed locally by MiniBankEnv.
        self.assertEqual(len(traj.steps[0].observation), 2)
        self.assertEqual(traj.final_answer, "C101 has two accounts.")

    def test_observation_sent_back_as_function_response(self):
        """Second request must carry the tool result as a Gemini function_response."""
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.side_effect = [
                _tool_response("get_customer", {"customer_id": "C101"}),
                _text_response("done"),
            ]
            task = get_task("waiver-c101")
            run_agent(
                task["task_id"], task["prompt"], MiniBankEnv(), make_gemini_model(),
                max_steps=task["max_steps"],
            )
        second = client.models.generate_content.call_args_list[1].kwargs["contents"]
        roles = [c.role for c in second]
        self.assertEqual(roles, ["user", "model", "user"])
        fr = second[-1].parts[0].function_response
        self.assertEqual(fr.name, "get_customer")
        self.assertIn("customer_id", json.dumps(fr.response, default=str))

    # --- function_response role regression (400 INVALID_ARGUMENT) ---

    def _second_request_contents(self, client):
        return client.models.generate_content.call_args_list[1].kwargs["contents"]

    def test_no_role_tool_sent_to_gemini(self):
        """Gemini rejects role='tool'. Every Content role must be user/model."""
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.side_effect = [
                _tool_response("get_account", {"customer_id": "C101"}),  # list obs
                _tool_response("calculate", {"op": "avg", "values": [1800, 1200]}),
                _text_response("done"),
            ]
            task = get_task("waiver-c101")
            run_agent(
                task["task_id"], task["prompt"], MiniBankEnv(), make_gemini_model(),
                max_steps=task["max_steps"],
            )
        for call in client.models.generate_content.call_args_list:
            for c in call.kwargs["contents"]:
                self.assertIn(c.role, ("user", "model"))

    def test_function_response_part_used_with_user_role(self):
        """Native representation: role='user' Content carrying a functionResponse part."""
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.side_effect = [
                _tool_response("get_customer", {"customer_id": "C101"}),
                _text_response("done"),
            ]
            task = get_task("waiver-c101")
            run_agent(
                task["task_id"], task["prompt"], MiniBankEnv(), make_gemini_model(),
                max_steps=task["max_steps"],
            )
        second = self._second_request_contents(client)
        fr_content = second[-1]
        self.assertEqual(fr_content.role, "user")
        self.assertEqual(len(fr_content.parts), 1)
        part = fr_content.parts[0]
        self.assertIsNotNone(part.function_response)
        self.assertEqual(part.function_response.name, "get_customer")
        self.assertIsInstance(part.function_response.response, dict)
        # the preceding turn is the model's own function_call
        self.assertEqual(second[-2].role, "model")
        self.assertEqual(second[-2].parts[0].function_call.name, "get_customer")

    def test_dict_observation_uses_function_response(self):
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.side_effect = [
                _tool_response("get_customer", {"customer_id": "C101"}),
                _text_response("done"),
            ]
            task = get_task("waiver-c101")
            run_agent(
                task["task_id"], task["prompt"], MiniBankEnv(), make_gemini_model(),
                max_steps=task["max_steps"],
            )
        resp = self._second_request_contents(client)[-1].parts[0].function_response.response
        self.assertIsInstance(resp, dict)
        self.assertIn("C101", json.dumps(resp, default=str))

    def test_list_observation_wrapped_but_recorded_unchanged(self):
        """get_account returns a list; the wire wrapper must not alter the record."""
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.side_effect = [
                _tool_response("get_account", {"customer_id": "C101"}),
                _text_response("done"),
            ]
            task = get_task("waiver-c101")
            traj = run_agent(
                task["task_id"], task["prompt"], MiniBankEnv(), make_gemini_model(),
                max_steps=task["max_steps"],
            )
        resp = self._second_request_contents(client)[-1].parts[0].function_response.response
        self.assertIsInstance(resp, dict)
        self.assertIn("result", resp)
        # Recorded observation is the original list, not the wrapper dict.
        self.assertIsInstance(traj.steps[0].observation, list)
        self.assertEqual(len(traj.steps[0].observation), 2)
        self.assertEqual(traj.steps[0].observation[0]["account_id"], "A101")

    def test_calculate_numeric_array_survives_round_trip(self):
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.side_effect = [
                _tool_response("get_account", {"customer_id": "C101"}),
                _tool_response("calculate", {"op": "avg", "values": [1800, 1200]}),
                _text_response("done"),
            ]
            task = get_task("waiver-c101")
            traj = run_agent(
                task["task_id"], task["prompt"], MiniBankEnv(), make_gemini_model(),
                max_steps=task["max_steps"],
            )
        # Request 1: task. Request 2: get_account result. Request 3: calculate result.
        out_call = client.models.generate_content.call_args_list[2].kwargs["contents"][-1]
        self.assertEqual(out_call.role, "user")
        self.assertEqual(out_call.parts[0].function_response.name, "calculate")
        self.assertEqual(out_call.parts[0].function_response.response, {"result": 1500})
        # The model's own calculate function_call carried the numeric array.
        calc_call = client.models.generate_content.call_args_list[2].kwargs["contents"][-2]
        self.assertEqual(calc_call.role, "model")
        self.assertEqual(calc_call.parts[0].function_call.args["values"], [1800, 1200])
        self.assertEqual(traj.steps[1].arguments["values"], [1800, 1200])
        self.assertEqual(traj.steps[1].observation, {"result": 1500})
        self.assertIsInstance(traj.steps[1].arguments["values"], list)

    def test_deferred_calls_still_auditable_after_role_fix(self):
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.side_effect = [
                _response([
                    _fc_part("get_customer", {"customer_id": "C101"}),
                    _fc_part("get_account", {"customer_id": "C101"}),
                ]),
                _text_response("done"),
            ]
            task = get_task("waiver-c101")
            traj = run_agent(
                task["task_id"], task["prompt"], MiniBankEnv(), make_gemini_model(),
                max_steps=task["max_steps"],
            )
        self.assertEqual(len(traj.steps), 1)
        self.assertEqual(
            traj.raw_history[2]["content"]["deferred_tool_calls"],
            [{"tool": "get_account", "arguments": {"customer_id": "C101"}}],
        )
        for call in client.models.generate_content.call_args_list:
            for c in call.kwargs["contents"]:
                self.assertIn(c.role, ("user", "model"))

    def test_multiple_calls_execute_one_and_record_the_rest(self):
        """Batched calls execute only the first; extras land in raw_history."""
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.side_effect = [
                _response([
                    _fc_part("get_customer", {"customer_id": "C101"}),
                    _fc_part("get_account", {"customer_id": "C101"}),
                ]),
                _text_response("Final."),
            ]
            task = get_task("waiver-c101")
            traj = run_agent(
                task["task_id"], task["prompt"], MiniBankEnv(), make_gemini_model(),
                max_steps=task["max_steps"],
            )
        self.assertEqual(traj.termination_reason, "agent_final")
        # Exactly one tool action was executed by the local environment.
        self.assertEqual(len(traj.steps), 1)
        self.assertEqual(traj.steps[0].action, "get_customer")
        # The dropped-from-execution call is still recorded, not silently lost.
        assistant_turn = traj.raw_history[2]["content"]
        self.assertEqual(
            assistant_turn["deferred_tool_calls"],
            [{"tool": "get_account", "arguments": {"customer_id": "C101"}}],
        )

    def test_provider_failure_becomes_model_error_termination(self):
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.side_effect = RuntimeError("500 internal")
            task = get_task("waiver-c101")
            traj = run_agent(
                task["task_id"], task["prompt"], MiniBankEnv(), make_gemini_model(),
                max_steps=task["max_steps"],
            )
        self.assertEqual(traj.termination_reason, "model_error")
        self.assertFalse(evaluate(task, traj)["passed"])

    def test_raw_history_preserved(self):
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.side_effect = [
                _tool_response("get_customer", {"customer_id": "C101"}),
                _text_response("done"),
            ]
            task = get_task("waiver-c101")
            traj = run_agent(
                task["task_id"], task["prompt"], MiniBankEnv(), make_gemini_model(),
                max_steps=task["max_steps"],
            )
        roles = [m["role"] for m in traj.raw_history]
        self.assertEqual(roles, ["system", "user", "assistant", "user", "assistant"])
        self.assertEqual(traj.raw_history[2]["content"]["tool"], "get_customer")
        self.assertIn("observation:", traj.raw_history[3]["content"])
        self.assertEqual(traj.raw_history[4]["content"], {"final": "done"})

    # --- runner dispatch ---

    def test_runner_dispatch_gemini(self):
        with patch("minilab.models.gemini.genai") as mock_genai:
            mock_genai.Client.return_value = MagicMock()
            self.assertTrue(callable(make_model("gemini")))
            _, kwargs = mock_genai.Client.call_args
            self.assertEqual(kwargs["api_key"], "dummy-gemini-key")

    def test_runner_dispatch_gemini_explicit_model(self):
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.return_value = _text_response("ok")
            model_fn = make_model("gemini:gemini-2.5-pro")
            model_fn(self.messages, TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertEqual(
            client.models.generate_content.call_args.kwargs["model"], "gemini-2.5-pro"
        )


class TestGeminiThoughtSignature(unittest.TestCase):
    """The model Content/Part must be echoed back verbatim.

    Gemini 3 rejects a follow-up request whose functionCall part has lost its
    thought_signature (400 INVALID_ARGUMENT), so the adapter must not rebuild
    Parts from name/arguments alone.
    """

    SIG = b"\xde\xad\xbe\xef-opaque-thought-signature"

    def setUp(self):
        self.key_env = os.environ.pop("GEMINI_API_KEY", None)
        os.environ["GEMINI_API_KEY"] = "dummy-gemini-key"

    def tearDown(self):
        if self.key_env is not None:
            os.environ["GEMINI_API_KEY"] = self.key_env
        else:
            os.environ.pop("GEMINI_API_KEY", None)

    def _second_contents(self, client):
        return client.models.generate_content.call_args_list[1].kwargs["contents"]

    # --- A / B: preservation and no reconstruction loss ---

    def test_a_signed_part_is_echoed_back_verbatim(self):
        original = _signed_part("get_customer", {"customer_id": "C101"}, self.SIG)
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.side_effect = [
                _response([original]),
                _text_response("done"),
            ]
            traj = _run("waiver-c101", make_gemini_model())
        self.assertEqual(traj.termination_reason, "agent_final")
        echoed = self._second_contents(client)[-2]
        # The exact same SDK Part object, not a copy or a rebuild.
        self.assertIs(echoed.parts[0], original)
        self.assertEqual(echoed.parts[0].thought_signature, self.SIG)
        self.assertEqual(echoed.parts[0].function_call.name, "get_customer")
        self.assertEqual(echoed.parts[0].function_call.args, {"customer_id": "C101"})

    def test_b_no_reconstruction_loss(self):
        original = _signed_part("calculate", {"op": "avg", "values": [1800, 1200]}, self.SIG)
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.side_effect = [
                _response([original]),
                _text_response("done"),
            ]
            _run("waiver-c101", make_gemini_model())
        echoed = self._second_contents(client)[-2].parts[0]
        self.assertEqual(echoed.thought_signature, self.SIG)
        self.assertEqual(echoed.function_call.args, {"op": "avg", "values": [1800, 1200]})
        # A Part built via the name/args factory would have no signature.
        self.assertIsNone(gtypes.Part.from_function_call(
            name="calculate", args={"op": "avg", "values": [1800, 1200]}
        ).thought_signature)

    def test_signature_survives_sdk_serialization(self):
        """The signature must still be on the wire, not just in memory."""
        original = _signed_part("get_customer", {"customer_id": "C101"}, self.SIG)
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.side_effect = [
                _response([original]),
                _text_response("done"),
            ]
            _run("waiver-c101", make_gemini_model())
        dumped = self._second_contents(client)[-2].model_dump(exclude_none=True)
        self.assertIn("thought_signature", dumped["parts"][0])
        self.assertEqual(dumped["parts"][0]["thought_signature"], self.SIG)

    # --- C: native function_response still correct ---

    def test_c_function_response_role_and_no_role_tool(self):
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.side_effect = [
                _signed_tool_response("get_customer", {"customer_id": "C101"}, self.SIG),
                _text_response("done"),
            ]
            _run("waiver-c101", make_gemini_model())
        for call in client.models.generate_content.call_args_list:
            for c in call.kwargs["contents"]:
                self.assertIn(c.role, ("user", "model"))
                self.assertNotEqual(c.role, "tool")
        fr = self._second_contents(client)[-1]
        self.assertEqual(fr.role, "user")
        self.assertIsNotNone(fr.parts[0].function_response)
        self.assertEqual(fr.parts[0].function_response.name, "get_customer")

    # --- D: two-turn integration ---

    def test_d_two_turn_integration(self):
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.side_effect = [
                _signed_tool_response("get_customer", {"customer_id": "C101"}, self.SIG),
                _text_response("C101 is a known customer."),
            ]
            traj = _run("waiver-c101", make_gemini_model())
        self.assertEqual(traj.termination_reason, "agent_final")
        self.assertEqual(traj.final_answer, "C101 is a known customer.")
        self.assertEqual(len(traj.steps), 1)
        self.assertEqual(traj.steps[0].action, "get_customer")
        second = self._second_contents(client)
        # 1: original signed model part, 2: user function_response
        self.assertEqual(second[-2].role, "model")
        self.assertEqual(second[-2].parts[0].thought_signature, self.SIG)
        self.assertEqual(second[-1].role, "user")
        self.assertEqual(second[-1].parts[0].function_response.name, "get_customer")

    # --- E: list observation ---

    def test_e_list_observation_wrapped_but_record_original(self):
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.side_effect = [
                _signed_tool_response("get_account", {"customer_id": "C101"}, self.SIG),
                _text_response("done"),
            ]
            traj = _run("waiver-c101", make_gemini_model())
        resp = self._second_contents(client)[-1].parts[0].function_response.response
        self.assertIsInstance(resp, dict)
        self.assertIn("result", resp)
        self.assertEqual(len(resp["result"]), 2)
        # Signature preserved on the model turn in the same request.
        self.assertEqual(self._second_contents(client)[-2].parts[0].thought_signature, self.SIG)
        # Recorded observation is the original list, not the wrapper.
        self.assertIsInstance(traj.steps[0].observation, list)
        self.assertEqual(traj.steps[0].observation[0]["account_id"], "A101")
        self.assertEqual(traj.steps[0].observation[1]["account_id"], "A102")

    # --- F: numeric array ---

    def test_f_numeric_array_and_signature_coexist(self):
        signed_calc = _signed_part("calculate", {"op": "avg", "values": [1800, 1200]}, self.SIG)
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.side_effect = [
                _response([signed_calc]),
                _text_response("done"),
            ]
            traj = _run("waiver-c101", make_gemini_model())
        echoed = self._second_contents(client)[-2].parts[0]
        self.assertEqual(echoed.thought_signature, self.SIG)
        self.assertEqual(echoed.function_call.args["values"], [1800, 1200])
        self.assertTrue(all(isinstance(v, (int, float)) for v in echoed.function_call.args["values"]))
        self.assertEqual(traj.steps[0].arguments["values"], [1800, 1200])
        self.assertEqual(traj.steps[0].observation, {"result": 1500})

    # --- G: missing signature ---

    def test_g_no_signature_is_not_invented(self):
        plain = _fc_part("get_customer", {"customer_id": "C101"})
        self.assertIsNone(plain.thought_signature)
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.side_effect = [
                _response([plain]),
                _text_response("done"),
            ]
            traj = _run("waiver-c101", make_gemini_model())
        self.assertEqual(traj.termination_reason, "agent_final")
        echoed = self._second_contents(client)[-2].parts[0]
        self.assertIsNone(echoed.thought_signature)
        self.assertEqual(echoed.function_call.name, "get_customer")

    def test_g_falls_back_to_reconstruction_without_stored_content(self):
        """_to_contents still rebuilds when no original Content was stored."""
        from minilab.models.gemini import _to_contents
        sys_inst, contents = _to_contents(
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": {"tool": "get_customer", "arguments": {"customer_id": "C101"}}},
                {"role": "user", "content": "observation: {}"},
            ]
        )
        self.assertEqual(sys_inst, "sys")
        self.assertEqual(contents[-2].role, "model")
        self.assertEqual(contents[-2].parts[0].function_call.name, "get_customer")
        self.assertIsNone(contents[-2].parts[0].thought_signature)

    # --- H: deferred calls ---

    def test_h_deferred_calls_auditable_with_signatures(self):
        p1 = _signed_part("get_customer", {"customer_id": "C101"}, b"sig-one")
        p2 = _signed_part("get_account", {"customer_id": "C101"}, b"sig-two")
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.side_effect = [
                _response([p1, p2]),
                _text_response("done"),
            ]
            traj = _run("waiver-c101", make_gemini_model())
        # Serial execution preserved: exactly one tool ran.
        self.assertEqual(len(traj.steps), 1)
        self.assertEqual(traj.steps[0].action, "get_customer")
        # Both calls remain auditable in raw_history.
        self.assertEqual(
            traj.raw_history[2]["content"]["deferred_tool_calls"],
            [{"tool": "get_account", "arguments": {"customer_id": "C101"}}],
        )
        # The whole original Content is echoed, signatures and
        # every part intact.
        echoed = self._second_contents(client)[-2]
        self.assertEqual(len(echoed.parts), 2)
        self.assertEqual(echoed.parts[0].thought_signature, b"sig-one")
        self.assertEqual(echoed.parts[1].thought_signature, b"sig-two")

    def test_h_signatures_accumulate_across_turns(self):
        sig1, sig2 = b"sig-turn-1", b"sig-turn-2"
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.side_effect = [
                _signed_tool_response("get_customer", {"customer_id": "C101"}, sig1),
                _signed_tool_response("get_account", {"customer_id": "C101"}, sig2),
                _text_response("done"),
            ]
            _run("waiver-c101", make_gemini_model())
        third = client.models.generate_content.call_args_list[2].kwargs["contents"]
        model_contents = [c for c in third if c.role == "model"]
        self.assertEqual(len(model_contents), 2)
        self.assertEqual([c.parts[0].thought_signature for c in model_contents], [sig1, sig2])

    # --- exact-failure regression ---

    def test_regression_signature_dropped_by_reconstruction(self):
        """Guards the bug: rebuilding the Part from name/args loses the signature."""
        signed = _signed_part("get_customer", {"customer_id": "C101"}, self.SIG)
        rebuilt = gtypes.Part.from_function_call(
            name=signed.function_call.name, args=signed.function_call.args
        )
        # This is exactly what the adapter used to send, and why Gemini 400'd.
        self.assertIsNone(rebuilt.thought_signature)
        # The fixed adapter sends the original object instead.
        with patch("minilab.models.gemini.genai") as mock_genai:
            client = mock_genai.Client.return_value
            client.models.generate_content.side_effect = [
                _response([signed]),
                _text_response("done"),
            ]
            _run("waiver-c101", make_gemini_model())
        self.assertEqual(
            self._second_contents(client)[-2].parts[0].thought_signature, self.SIG
        )


if __name__ == "__main__":
    unittest.main()