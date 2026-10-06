"""OpenAI LLM adapter for the Mini Agent Reliability Lab.

This adapter conforms to the model_fn interface expected by the agent loop:
    model_fn(messages, tool_schemas, config) -> {"tool", "arguments"} | {"final"}

It uses the OpenAI Chat Completions API with tool calling.

Environment variables:
    OPENAI_API_KEY: The API key for accessing the OpenAI API.

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


def make_openai_model(
    model_name: str,
    base_url: str = None,
    api_key_env: str = "OPENAI_API_KEY",
    default_api_key: str = None,
):
    """Return a model_fn compatible with the agent loop for an OpenAI-compatible API.

    Args:
        model_name: The model ID to send. Sent verbatim; never rewritten.
        base_url: Optional OpenAI-compatible endpoint. None (the default) targets
            the public OpenAI API, preserving existing OpenAI behavior.
        api_key_env: Environment variable holding the API key.
        default_api_key: Used when api_key_env is unset. For local servers that
            do not authenticate.

    Returns:
        A callable that takes (messages, tool_schemas, config) and returns either
        a tool call dict or a final answer dict.

    Raises:
        ValueError: If the API key cannot be resolved.
        ImportError: If the openai package is not installed.
    """
    if OpenAI is None:
        raise ValueError(
            "The openai package is not installed. Install it with `pip install openai` "
            "to use the OpenAI adapter."
        ) from _import_error

    api_key = os.environ.get(api_key_env) or default_api_key
    if not api_key:
        raise ValueError(
            f"{api_key_env} environment variable not set. "
            f"Set it to your OpenAI API key to use the adapter."
        )

    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)

    def _convert_tools(tool_schemas: List[Dict]) -> List[Dict]:
        """Convert our internal tool schemas to OpenAI tools format."""
        openai_tools = []
        for tool in tool_schemas:
            name = tool["name"]
            desc = tool.get("description", "")
            params = tool.get("params", {})
            # Build JSON Schema for parameters
            properties: Dict[str, Any] = {}
            required: List[str] = []
            for param_name, spec in params.items():
                param_type = spec.get("type", "string")
                # Map simple types to JSON Schema types
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
        """Call the OpenAI API and convert the response to our expected format."""
        # Convert tools
        tools = _convert_tools(tool_schemas)

        # Extract parameters from config
        temperature = config.get("temperature", 0.0)
        # Note: We do not have max_tokens in config; we could add it later if needed.
        # For now, we rely on the model's default.

        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=messages,
                tools=tools,
                tool_choice="auto",  # Let the model decide when to use a tool
                temperature=temperature,
            )
        except Exception as e:
            # Propagate any exception so the agent loop can catch it
            raise e

        choice = response.choices[0]
        if choice.tool_calls:
            # We assume at most one tool call (as per our agent loop's design)
            tool_call = choice.tool_calls[0]
            # The arguments are a JSON string
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
            # No tool call: treat the message content as the final answer
            content = choice.message.content
            if content is None:
                content = ""
            return {
                "final": content
            }

    return model_fn
