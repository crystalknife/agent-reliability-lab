"""Real OpenAI SDK chat-completion objects for adapter tests.

Responses are built with the SDK's own validator (ChatCompletion.model_validate), so a test
can only use fields the SDK really has. MagicMock responses invented `choice.tool_calls`,
which no SDK version provides, and hid an adapter bug for that reason.
"""

import json

from openai.types.chat import ChatCompletion


def _call(i, name, arguments):
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments)
    return {"id": f"call_{i}", "type": "function", "function": {"name": name, "arguments": arguments}}


def chat_completion(*, content=None, tool_calls=(), finish_reason=None):
    """tool_calls: sequence of (name, arguments); arguments is a dict or a raw JSON string."""
    calls = [_call(i, name, args) for i, (name, args) in enumerate(tool_calls)]
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = calls
    return ChatCompletion.model_validate({
        "id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": "test-model",
        "choices": [{"index": 0, "message": message,
                     "finish_reason": finish_reason or ("tool_calls" if calls else "stop")}],
    })


def tool_call_response(name, arguments, *, finish_reason=None):
    return chat_completion(tool_calls=[(name, arguments)], finish_reason=finish_reason)


def text_response(content):
    return chat_completion(content=content)
