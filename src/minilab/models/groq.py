"""Groq LLM adapter for the Mini Agent Reliability Lab.

Conforms to the model_fn interface expected by the agent loop:
    model_fn(messages, tool_schemas, config) -> {"tool", "arguments"} | {"final"}

Uses Groq's OpenAI-compatible endpoint with local function/tool calling.
The adapter never executes a tool; it only translates between the
internal MiniLab protocol and the OpenAI-compatible wire format.

Environment variables:
    GROQ_API_KEY: The API key for the Groq API.

No retries, no fallback models, no automatic provider switching. Provider
errors propagate so the agent loop records a model_error termination.
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

DEFAULT_MODEL = "openai/gpt-oss-120b"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"


def make_groq_model(model_name: str = DEFAULT_MODEL):
    """Return a model_fn compatible with the agent loop for Groq.

    Args:
        model_name: Groq model ID. Defaults to "openai/gpt-oss-120b".

    Returns:
        Callable (messages, tool_schemas, config) -> {"tool", "arguments"} | {"final"}

    Raises:
        ValueError: If the openai package or GROQ_API_KEY is unavailable.
    """
    if OpenAI is None:
        raise ValueError(
            "The openai package is not installed. Install it with `pip install openai` "
            "to use the Groq adapter."
        ) from _import_error

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY environment variable not set. "
            "Set it to your Groq API key to use the adapter."
        )

    client = OpenAI(
        api_key=api_key,
        base_url=GROQ_BASE_URL,
    )

    def _convert_tools(tool_schemas: List[Dict]) -> List[Dict]:
        """Convert MiniLab tool schemas to OpenAI-compatible function tools."""
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
                # Carry through nested JSON Schema keywords such as items.
                # Needed for array params (calculate.values) to be typed correctly.
                if "items" in spec:
                    prop["items"] = spec["items"]
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
        """Call the Groq API and convert the response to the internal format.

        temperature=0 is passed through as configuration only; it is not a
        guarantee of deterministic model behavior.
        """
        tools = _convert_tools(tool_schemas)
        temperature = config.get("temperature", 0.0)

        response = client.chat.completions.create(
            model=model_name,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=temperature,
        )

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
        content = choice.message.content
        if content is None:
            content = ""
        return {"final": content}

    return model_fn