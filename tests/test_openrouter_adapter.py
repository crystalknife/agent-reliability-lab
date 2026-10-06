"""Tests for the OpenRouter adapter."""

import json
import os
import unittest
from unittest.mock import patch, MagicMock

from minilab.models.openrouter import make_openrouter_model, DEFAULT_MODEL, _import_error


class TestOpenRouterAdapter(unittest.TestCase):
    def setUp(self):
        # Store the current API key and set a dummy one for tests that need it
        self.api_key_env = os.environ.pop("OPENROUTER_API_KEY", None)
        self.referer_env = os.environ.pop("OPENROUTER_REFERER", None)
        self.title_env = os.environ.pop("OPENROUTER_TITLE", None)
        os.environ["OPENROUTER_API_KEY"] = "dummy-key-for-testing"

    def tearDown(self):
        # Restore the original environment variables
        if self.api_key_env is not None:
            os.environ["OPENROUTER_API_KEY"] = self.api_key_env
        else:
            os.environ.pop("OPENROUTER_API_KEY", None)
        if self.referer_env is not None:
            os.environ["OPENROUTER_REFERER"] = self.referer_env
        else:
            os.environ.pop("OPENROUTER_REFERER", None)
        if self.title_env is not None:
            os.environ["OPENROUTER_TITLE"] = self.title_env
        else:
            os.environ.pop("OPENROUTER_TITLE", None)

    def test_import_error_if_openai_not_installed(self):
        # Simulate the openai package not being available
        import minilab.models.openrouter as mod
        original_openai = mod.OpenAI
        mod.OpenAI = None
        try:
            with self.assertRaises(ValueError) as cm:
                make_openrouter_model()
            self.assertIn("The openai package is not installed", str(cm.exception))
        finally:
            mod.OpenAI = original_openai

    def test_missing_api_key_raises(self):
        os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            with self.assertRaises(ValueError) as cm:
                make_openrouter_model()
            self.assertIn("OPENROUTER_API_KEY environment variable not set", str(cm.exception))
        finally:
            os.environ["OPENROUTER_API_KEY"] = "dummy-key-for-testing"

    @patch("minilab.models.openrouter.OpenAI")
    def test_client_constructed_with_correct_base_url(self, mock_openai_class):
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.tool_calls = []
        mock_choice.message.content = "Test response"
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response

        model_fn = make_openrouter_model()
        messages = [{"role": "user", "content": "hello"}]
        tool_schemas = []
        config = {"temperature": 0.0}
        model_fn(messages, tool_schemas, config)

        # Verify OpenAI client was constructed with correct base_url
        mock_openai_class.assert_called_once()
        call_args, call_kwargs = mock_openai_class.call_args
        self.assertEqual(call_kwargs["base_url"], "https://openrouter.ai/api/v1")
        self.assertEqual(call_kwargs["api_key"], "dummy-key-for-testing")

    @patch("minilab.models.openrouter.OpenAI")
    def test_correct_model_id_passed(self, mock_openai_class):
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.tool_calls = []
        mock_choice.message.content = "Test response"
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response

        # Test default model
        model_fn = make_openrouter_model()
        messages = [{"role": "user", "content": "hello"}]
        tool_schemas = []
        config = {"temperature": 0.0}
        model_fn(messages, tool_schemas, config)

        mock_client.chat.completions.create.assert_called_once()
        call_args, call_kwargs = mock_client.chat.completions.create.call_args
        self.assertEqual(call_kwargs["model"], "nvidia/nemotron-3-ultra-550b-a55b:free")

        # Test custom model
        mock_client.chat.completions.create.reset_mock()
        custom_model = "custom/model:free"
        model_fn2 = make_openrouter_model(custom_model)
        model_fn2(messages, tool_schemas, config)
        call_args, call_kwargs = mock_client.chat.completions.create.call_args
        self.assertEqual(call_kwargs["model"], custom_model)

    @patch("minilab.models.openrouter.OpenAI")
    def test_tool_call_parsing(self, mock_openai_class):
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.tool_calls = [MagicMock()]
        mock_choice.tool_calls[0].function.name = "get_account"
        mock_choice.tool_calls[0].function.arguments = json.dumps(
            {"customer_id": "C101"}
        )
        mock_choice.message.content = None
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response

        model_fn = make_openrouter_model()
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

        result = model_fn(messages, tool_schemas, config)

        self.assertEqual(result["tool"], "get_account")
        self.assertEqual(result["arguments"], {"customer_id": "C101"})

    @patch("minilab.models.openrouter.OpenAI")
    def test_json_tool_arguments_parsed(self, mock_openai_class):
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.tool_calls = [MagicMock()]
        mock_choice.tool_calls[0].function.name = "calculate"
        mock_choice.tool_calls[0].function.arguments = json.dumps(
            {"op": "avg", "values": [1800, 1200]}
        )
        mock_choice.message.content = None
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response

        model_fn = make_openrouter_model()
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

        result = model_fn(messages, tool_schemas, config)

        self.assertEqual(result["tool"], "calculate")
        self.assertEqual(result["arguments"], {"op": "avg", "values": [1800, 1200]})

    @patch("minilab.models.openrouter.OpenAI")
    def test_text_response_becomes_final_answer(self, mock_openai_class):
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.tool_calls = []
        mock_choice.message.content = "The customer qualifies for the fee waiver."
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response

        model_fn = make_openrouter_model()
        messages = [{"role": "user", "content": "hello"}]
        tool_schemas = []
        config = {"temperature": 0.0}

        result = model_fn(messages, tool_schemas, config)

        self.assertEqual(result["final"], "The customer qualifies for the fee waiver.")

    @patch("minilab.models.openrouter.OpenAI")
    def test_malformed_json_propagates_as_error(self, mock_openai_class):
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

        model_fn = make_openrouter_model()
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

    @patch("minilab.models.openrouter.OpenAI")
    def test_no_retry_or_fallback(self, mock_openai_class):
        # Ensure the adapter doesn't catch and retry on API errors
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client
        mock_client.chat.completions.create.side_effect = ValueError("API error")

        model_fn = make_openrouter_model()
        messages = [{"role": "user", "content": "hello"}]
        tool_schemas = []
        config = {"temperature": 0.0}

        with self.assertRaises(ValueError) as cm:
            model_fn(messages, tool_schemas, config)
        self.assertEqual(str(cm.exception), "API error")
        # Verify only one call was made (no retries)
        self.assertEqual(mock_client.chat.completions.create.call_count, 1)


if __name__ == "__main__":
    unittest.main()