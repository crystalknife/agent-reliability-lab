"""OpenRouter LLM adapter for the Mini Agent Reliability Lab.

This adapter conforms to the model_fn interface expected by the agent loop:
    model_fn(messages, tool_schemas, config) -> {"tool", "arguments"} | {"final"}

It uses the OpenRouter OpenAI-compatible API with tool calling.

Environment variables:
    OPENROUTER_API_KEY: The API key for accessing the OpenRouter API.

The adapter does not perform any retries; errors are propagated to the agent loop,
which will convert them into a model_error termination.
"""

import json
import os
from typing import Any, Dict, List, Union

try:
    from openai import OpenAI
except ImportError as e:
    OpenAI = None  # type: ignore
    _import_error = e
else:
    _import_error = None

# Fixed model ID as specified
DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def make_openrouter_model(model_name: str = DEFAULT_MODEL):
    """Return a model_fn compatible with the agent loop for OpenRouter.

    Args:
        model_name: The model ID to use on OpenRouter.
                    Defaults to "nvidia/nemotron-3-ultra-550b-a55b:free".

    Returns:
        A callable that takes (messages, tool_schemas, config) and returns either
        a tool call dict or a final answer dict.

    Raises:
        ValueError: If the OPENROUTER_API_KEY environment variable is not set.
        ImportError: If the openai package is not installed.
    """
    if OpenAI is None:
        raise ValueError(
            "The openai package is not installed. Install it with `pip install openai` "
            "to use the OpenRouter adapter."
        ) from _import_error

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENROUTER_API_KEY environment variable not set. "
            "Set it to your OpenRouter API key to use the adapter."
        )

    client = OpenAI(
        api_key=api_key,
        base_url=OPENROUTER_BASE_URL,
    )

    def _convert_tools(tool_schemas: List[Dict]) -> List[Dict]:
        """Convert our internal tool schemas to OpenAI tools format."""
        openai_tools = []
        for tool in tool_schemas:
            name = tool["name"]
            desc = tool.get("description", "")
            params = tool.get("params", {})
            properties: Dict[str, Any] = {}
            required: List[str] = []
            for param_name, spec in params.items():
                param_type = spec.get("type", "string")
                type_map = {
                    "string": "string",
                    "integer": "integer",
                    "number": "number",
                    "boolean": "boolean",
                    "array": "array",
                    "object": "object",
                }
                json_schema_type = type_map.get(param_type, "string")
                prop: Dict[str, Any] = {"type": json_schema_type}
                if "description" in spec:
                    prop["description"] = spec["description"]
                properties[param_name] = prop
                if spec.get("required"):
                    required.append(param_name)
            schema: Dict[str, Any] = {
                "type": "object",
                "properties": properties,
                "required": required
            }
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": schema
                }
            })
        return openai_tools

    def model_fn(
        messages: List[Dict],
        tool_schemas: List[Dict],
        config: Dict
    ) -> Union[Dict[str, Any], Dict[str, str]]:
        """Call the OpenRouter API and convert the response to our expected format."""
        tools = _convert_tools(tool_schemas)

        temperature = config.get("temperature", 0.0)

        # Optional headers per OpenRouter recommendations
        extra_headers = {}
        referer = os.environ.get("OPENROUTER_REFERER")
        if referer:
            extra_headers["HTTP-Referer"] = referer
        title = os.environ.get("OPENROUTER_TITLE")
        if title:
            extra_headers["X-Title"] = title

        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=temperature,
                extra_headers=extra_headers if extra_headers else None,
            )
        except Exception as e:
            raise e

        choice = response.choices[0]
        if choice.tool_calls:
            tool_call = choice.tool_calls[0]
            try:
                args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"Invalid JSON in tool call arguments for tool {tool_call.function.name}: "
                    f"{tool_call.function.arguments}"
                ) from e
            return {
                "tool": tool_call.function.name,
                "arguments": args
            }
        else:
            content = choice.message.content
            if content is None:
                content = ""
            return {
                "final": content
            }

    return model_fn