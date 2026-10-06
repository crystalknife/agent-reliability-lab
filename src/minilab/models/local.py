"""Local llama.cpp adapter for the Mini Agent Reliability Lab.

The local server exposes an OpenAI-compatible /v1 chat completions endpoint,
but this adapter does NOT reuse make_openai_model: that helper (like the other
OpenAI-compatible adapters) reads tool calls from `choice.tool_calls`, which
does not exist on real OpenAI SDK responses (they live at
`choice.message.tool_calls`). Reusing it would turn every real tool call into
an AttributeError. This adapter is therefore self-contained and parses the
real response shape.

    model_fn(messages, tool_schemas, config) -> {"tool", "arguments"} | {"final"}

The server is external: never started from here, no retries, no fallbacks, no
model switching. The configured model ID is sent verbatim; llama-server
ignores it (single model loaded) but the field is required. Server errors
propagate to the agent loop as a model_error termination, which the existing
trial-status handling classifies as provider_error, never an agent failure.

Environment variables:
    LOCAL_API_KEY: Optional override. The server does not authenticate; a
        placeholder is sent when this is unset. OPENAI_API_KEY is never read.
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

LOCAL_BASE_URL = "http://127.0.0.1:8081/v1"
DEFAULT_MODEL = "qwen3-4b"
MODEL_VERSION = "llama.cpp-b11435"
API_KEY_ENV = "LOCAL_API_KEY"
# Placeholder: the local server ignores it, but the OpenAI client requires a value.
PLACEHOLDER_API_KEY = "local-noauth"

OBSERVATION_PREFIX = "observation: "


def to_api_messages(messages):
    """Translate MiniLab history into OpenAI-compatible messages.

    The agent loop records its own actions as assistant messages with a dict
    `content` ({"tool", "arguments"}) and tool results as user messages
    prefixed with "observation: ". llama.cpp rejects both shapes (HTTP 400:
    "Expected 'content' to be a string or an array"), so they become a proper
    assistant message with `tool_calls` followed by a `tool`-role message.

    Tool call IDs cannot round-trip: the internal protocol carries no id slot,
    so server-provided ids are lost before the next turn. Generated ids are
    positional (call_0, call_1, ...) and therefore stable across turns; only
    within-request consistency matters to the server.
    """
    out = []
    pending = []
    counter = 0
    for m in messages:
        role, content = m.get("role"), m.get("content")
        if role == "assistant" and isinstance(content, dict) and "tool" in content:
            call_id = f"call_{counter}"
            counter += 1
            pending.append(call_id)
            out.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": content["tool"],
                        "arguments": json.dumps(content.get("arguments", {}) or {}),
                    },
                }],
            })
        elif (role == "user" and isinstance(content, str)
                and content.startswith(OBSERVATION_PREFIX) and pending):
            out.append({"role": "tool", "tool_call_id": pending.pop(0),
                        "content": content[len(OBSERVATION_PREFIX):]})
        else:
            out.append(m)
    return out


def make_local_model(model_name: str = DEFAULT_MODEL):
    """Return a model_fn for the local llama-server.

    Args:
        model_name: Sent verbatim as the request model. Defaults to "qwen3-4b".
    """
    if OpenAI is None:
        raise ValueError(
            "The openai package is not installed. Install it with `pip install openai` "
            "to use the local adapter."
        ) from _import_error

    api_key = os.environ.get(API_KEY_ENV) or PLACEHOLDER_API_KEY
    client = OpenAI(api_key=api_key, base_url=LOCAL_BASE_URL)

    def _convert_tools(tool_schemas: List[Dict]) -> List[Dict]:
        openai_tools = []
        for tool in tool_schemas:
            properties: Dict[str, Any] = {}
            required: List[str] = []
            for param_name, spec in tool.get("params", {}).items():
                prop: Dict[str, Any] = {"type": spec.get("type", "string")}
                if "description" in spec:
                    prop["description"] = spec["description"]
                if "items" in spec:
                    prop["items"] = spec["items"]
                properties[param_name] = prop
                if spec.get("required"):
                    required.append(param_name)
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": {"type": "object", "properties": properties,
                                   "required": required},
                },
            })
        return openai_tools

    def model_fn(
        messages: List[Dict],
        tool_schemas: List[Dict],
        config: Dict,
    ) -> Union[Dict[str, Any], Dict[str, str]]:
        response = client.chat.completions.create(
            model=model_name,
            messages=to_api_messages(messages),
            tools=_convert_tools(tool_schemas),
            tool_choice="auto",
            temperature=config.get("temperature", 0.0),
        )
        message = response.choices[0].message
        # Real OpenAI SDK shape: tool calls live on the message, not the choice.
        tool_calls = message.tool_calls
        if tool_calls:
            call = tool_calls[0]
            try:
                args = json.loads(call.function.arguments)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"Invalid JSON in tool call arguments for tool {call.function.name}: "
                    f"{call.function.arguments}"
                ) from e
            return {"tool": call.function.name, "arguments": args}
        content = message.content
        return {"final": content if content is not None else ""}

    return model_fn
