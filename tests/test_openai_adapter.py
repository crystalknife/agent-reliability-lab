"""Tests for the OpenAI adapter."""

import json
import os
import unittest
from unittest.mock import patch, MagicMock

from minilab.models.openai import make_openai_model, _import_error


class TestOpenAIAdapter(unittest.TestCase):
    def setUp(self):
        # Store the current API key and set a dummy one for tests that need it
        self.api_key_env = os.environ.pop("OPENAI_API_KEY", None)
        os.environ["OPENAI_API_KEY"] = "dummy-key-for-testing"

    def tearDown(self):
        # Restore the original API key state
        if self.api_key_env is not None:
            os.environ["OPENAI_API_KEY"] = self.api_key_env
        else:
            os.environ.pop("OPENAI_API_KEY", None)

    def test_import_error_if_openai_not_installed(self):
        # Simulate the openai package not being available
        # We'll temporarily replace OpenAI with None in the module
        import minilab.models.openai as mod
        original_openai = mod.OpenAI
        mod.OpenAI = None
        try:
            with self.assertRaises(ValueError) as cm:
                make_openai_model("gpt-4o")
            self.assertIn("The openai package is not installed", str(cm.exception))
        finally:
            mod.OpenAI = original_openai

    def test_missing_api_key_raises(self):
        # Temporarily remove the API key for this test
        os.environ.pop("OPENAI_API_KEY", None)
        try:
            with self.assertRaises(ValueError) as cm:
                make_openai_model("gpt-4o")
            self.assertIn("OPENAI_API_KEY environment variable not set", str(cm.exception))
        finally:
            # Restore the dummy key for other tests
            os.environ["OPENAI_API_KEY"] = "dummy-key-for-testing"

    @patch("minilab.models.openai.OpenAI")
    def test_tool_call_response(self, mock_openai_class):
        # Setup mock
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.tool_calls = [MagicMock()]
        mock_choice.tool_calls[0].function.name = "get_account"
        mock_choice.tool_calls[0].function.arguments = json.dumps(
            {"customer_id": "C101"}
        )
        mock_choice.message.content = None  # Not used when tool_calls present
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response

        # Create the model_fn
        model_fn = make_openai_model("gpt-4o")
        # Provide dummy inputs
        messages = [{"role": "user", "content": "hello"}]
        tool_schemas = [
            {
                "name": "get_account",
                "description": "Get account info",
                "params": {
                    "customer_id": {"type": "string", "required": True}
                }
            }
        ]
        config = {"temperature": 0.0}

        # Call the model_fn
        result = model_fn(messages, tool_schemas, config)

        # Verify the result
        self.assertEqual(result["tool"], "get_account")
        self.assertEqual(result["arguments"], {"customer_id": "C101"})

        # Verify the API was called with the correct parameters
        mock_client.chat.completions.create.assert_called_once()
        call_args, call_kwargs = mock_client.chat.completions.create.call_args
        self.assertEqual(call_kwargs["model"], "gpt-4o")
        self.assertEqual(call_kwargs["messages"], messages)
        self.assertEqual(call_kwargs["temperature"], 0.0)
        # Check that tools were passed and converted correctly
        self.assertIn("tools", call_kwargs)
        tools = call_kwargs["tools"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["type"], "function")
        self.assertEqual(tools[0]["function"]["name"], "get_account")
        self.assertEqual(
            tools[0]["function"]["description"],
            "Get account info"
        )
        # Check the parameters schema
        params = tools[0]["function"]["parameters"]
        self.assertEqual(params["type"], "object")
        self.assertIn("properties", params)
        self.assertIn("customer_id", params["properties"])
        self.assertEqual(params["properties"]["customer_id"]["type"], "string")
        self.assertEqual(params["required"], ["customer_id"])

    @patch("minilab.models.openai.OpenAI")
    def test_final_answer_response(self, mock_openai_class):
        # Setup mock
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.tool_calls = []  # No tool calls
        mock_choice.message.content = "The customer qualifies."
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response

        model_fn = make_openai_model("gpt-4o")
        messages = [{"role": "user", "content": "hello"}]
        tool_schemas = []  # empty is fine
        config = {"temperature": 0.0}

        result = model_fn(messages, tool_schemas, config)

        self.assertEqual(result["final"], "The customer qualifies.")

    @patch("minilab.models.openai.OpenAI")
    def test_malformed_tool_call_arguments(self, mock_openai_class):
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.tool_calls = [MagicMock()]
        mock_choice.tool_calls[0].function.name = "calculate"
        mock_choice.tool_calls[0].function.arguments = "not valid json"
        mock_choice.message.content = None
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response

        model_fn = make_openai_model("gpt-4o")
        messages = [{"role": "user", "content": "hello"}]
        tool_schemas = [
            {
                "name": "calculate",
                "description": "Do arithmetic",
                "params": {
                    "op": {"type": "string", "required": True},
                    "values": {"type": "array", "required": True}
                }
            }
        ]
        config = {"temperature": 0.0}

        with self.assertRaises(ValueError) as cm:
            model_fn(messages, tool_schemas, config)
        self.assertIn("Invalid JSON in tool call arguments", str(cm.exception))

    @patch("minilab.models.openai.OpenAI")
    def test_api_exception_propagated(self, mock_openai_class):
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_client.chat.completions.create.side_effect = ValueError("API error")

        model_fn = make_openai_model("gpt-4o")
        messages = [{"role": "user", "content": "hello"}]
        tool_schemas = []
        config = {"temperature": 0.0}

        with self.assertRaises(ValueError) as cm:
            model_fn(messages, tool_schemas, config)
        self.assertEqual(str(cm.exception), "API error")


if __name__ == "__main__":
    unittest.main()
