"""Google Gemini native adapter for the Mini Agent Reliability Lab.

Conforms to the model_fn interface expected by the agent loop:
    model_fn(messages, tool_schemas, config) -> {"tool", "arguments"} | {"final"}

Uses the official Google Gen AI SDK (google-genai) against the Gemini
Developer API with native function calling. The adapter only translates
between MiniLab's internal protocol and Gemini's function-call response;
it never executes a tool. agent.py / MiniBankEnv stays the sole executor.

Only the five local MiniBank tools are ever exposed. No Google Search
grounding, Maps, code execution, URL context, or any hosted/remote tool is
enabled, and automatic function calling is disabled so the SDK never runs
anything on our behalf.

Environment variables:
    GEMINI_API_KEY: The API key for the Gemini Developer API.

No retries, no fallback models, no automatic provider switching. Provider
errors propagate so the agent loop records a model_error termination.
"""

import json
import os
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from google import genai
    from google.genai import types
except ImportError as e:  # pragma: no cover - environment dependent
    genai = None  # type: ignore
    types = None  # type: ignore
    _import_error = e
else:
    _import_error = None

DEFAULT_MODEL = "gemini-2.5-flash"

_TYPE_MAP = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "array": "array",
    "object": "object",
}


def _normalize_args(args: Any) -> Dict[str, Any]:
    """Return Gemini function-call args as a JSON-compatible dict.

    The SDK already yields a dict, so numeric arrays (calculate.values)
    stay arrays of numbers. A JSON string is accepted for robustness and
    raises on malformed input; anything else is an explicit error.
    """
    if args is None:
        return {}
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in function call arguments: {args}") from e
        if not isinstance(parsed, dict):
            raise ValueError("function call arguments must decode to an object")
        return parsed
    raise ValueError(f"Unsupported function call arguments type: {type(args)!r}")


def _convert_tools(tool_schemas: List[Dict]) -> List[Any]:
    """Convert MiniLab TOOL_SCHEMAS into a single Gemini Tool of declarations.

    Names, descriptions, parameter types, required lists, and array `items`
    are preserved exactly. No hosted tools are added.
    """
    declarations = []
    for tool in tool_schemas:
        params = tool.get("params", {})
        properties: Dict[str, Any] = {}
        required: List[str] = []
        for pname, spec in params.items():
            ptype = _TYPE_MAP.get(spec.get("type", "string"), "string")
            prop: Dict[str, Any] = {"type": ptype}
            if "description" in spec:
                prop["description"] = spec["description"]
            if "items" in spec:
                prop["items"] = spec["items"]
            properties[pname] = prop
            if spec.get("required"):
                required.append(pname)
        declarations.append(
            types.FunctionDeclaration(
                name=tool["name"],
                description=tool.get("description", ""),
                parameters_json_schema={
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            )
        )
    return [types.Tool(function_declarations=declarations)]


def _to_contents(
    messages: List[Dict],
    model_contents: Optional[List[Any]] = None,
) -> Tuple[Optional[str], List[Any]]:
    """Translate MiniLab chat messages into (system_instruction, Gemini contents).

    MiniLab encodes a tool exchange as an assistant dict {"tool", "arguments"}
    followed by a user message "observation: {json}". Gemini expects the model's
    own function_call content followed by a user-role function_response part.
    The system message becomes system_instruction and is not part of contents.

    `model_contents` holds the original types.Content objects returned by Gemini,
    in the order the corresponding function calls were issued. They are reused
    verbatim so opaque model metadata such as Part.thought_signature survives.
    Rebuilding a Part from name/arguments alone would drop it and Gemini rejects
    the follow-up request. When no stored Content is available the function call
    is reconstructed as before, which is all a model that returns no signature
    needs.
    """
    system_instruction: Optional[str] = None
    contents: List[Any] = []
    pending_tool: Optional[str] = None
    stored = list(model_contents or [])
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            system_instruction = content if isinstance(content, str) else json.dumps(content, default=str)
        elif role == "assistant" and isinstance(content, dict) and "tool" in content:
            name = content["tool"]
            args = content.get("arguments", {}) or {}
            pending_tool = name
            if stored:
                # Original Gemini Content, preserving thought_signature etc.
                contents.append(stored.pop(0))
            else:
                # Reconstruct only when no original Content was stored. Parts
                # built this way carry no thought_signature.
                contents.append(
                    types.Content(
                        role="model",
                        parts=[types.Part.from_function_call(name=name, args=args)],
                    )
                )
        elif role == "user":
            if isinstance(content, str) and content.startswith("observation: "):
                obs_str = content[len("observation: "):]
                try:
                    obs = json.loads(obs_str)
                except json.JSONDecodeError:
                    obs = {"raw": obs_str}
                # Gemini requires function_response.response to be an object;
                # tools returning a list or scalar are wrapped without altering
                # the recorded observation.
                if not isinstance(obs, dict):
                    obs = {"result": obs}
                name = pending_tool or "tool"
                # The function_response part carries the tool result. Its Content
                # role must be "user", not "tool": google-genai types.Content
                # documents role as "either 'user' or 'model'", and the SDK's own
                # function-calling loop (chats.py) wraps function_response parts
                # in a role="user" Content. Sending role="tool" is rejected
                # server-side with 400 INVALID_ARGUMENT.
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_function_response(name=name, response=obs)],
                    )
                )
                pending_tool = None
            else:
                text = content if isinstance(content, str) else json.dumps(content, default=str)
                contents.append(types.Content(role="user", parts=[types.Part.from_text(text=text)]))
        else:
            contents.append(
                types.Content(role="user", parts=[types.Part.from_text(text=str(content))])
            )
    return system_instruction, contents


def _parse_response(response: Any) -> Tuple[List[Any], str]:
    """Split a Gemini response into (function_calls, concatenated_text)."""
    parts = response.candidates[0].content.parts or []
    function_calls: List[Any] = []
    text_chunks: List[str] = []
    for p in parts:
        fc = getattr(p, "function_call", None)
        if fc is not None and getattr(fc, "name", None):
            function_calls.append(fc)
        t = getattr(p, "text", None)
        if t:
            text_chunks.append(t)
    return function_calls, "".join(text_chunks)


def _model_content(response: Any) -> Any:
    """Return the candidate's original Content object, unmodified.

    This is the SDK object Gemini produced. It is stored and echoed back as-is so
    Part-level metadata (notably thought_signature, and any thought text parts)
    reaches the API exactly as returned.
    """
    return response.candidates[0].content


def make_gemini_model(model_name: str = DEFAULT_MODEL):
    """Return a model_fn compatible with the agent loop for Gemini.

    Args:
        model_name: Gemini model ID. Defaults to "gemini-2.5-flash".

    Returns:
        Callable (messages, tool_schemas, config) -> {"tool", "arguments"} | {"final"}

    Raises:
        ValueError: If the google-genai package or GEMINI_API_KEY is unavailable.
    """
    if genai is None or types is None:
        raise ValueError(
            "The google-genai package is not installed. Install it with "
            "`pip install google-genai` to use the Gemini adapter."
        ) from _import_error

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError(
            "GEMINI_API_KEY environment variable not set. "
            "Set it to your Gemini API key to use the adapter."
        )

    client = genai.Client(api_key=api_key)

    # Original model Content objects from each function-calling response, in
    # order. Gemini 3 requires the returned functionCall part, including its
    # opaque Part.thought_signature, to be echoed back verbatim on the next
    # request when history is managed manually. Keeping the SDK objects (rather
    # than rebuilding Parts from name/args) preserves that metadata untouched.
    model_contents: List[Any] = []

    def model_fn(
        messages: List[Dict],
        tool_schemas: List[Dict],
        config: Dict
    ) -> Union[Dict[str, Any], Dict[str, str]]:
        """Call Gemini and convert the response to the internal format.

        temperature=0 is passed through as configuration only; it is not a
        guarantee of deterministic model behavior.
        """
        system_instruction, contents = _to_contents(messages, model_contents)
        tools = _convert_tools(tool_schemas)
        temperature = config.get("temperature", 0.0)
        gen_config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=tools,
            temperature=temperature,
            # Never let the SDK execute anything; we want raw function calls.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=gen_config,
        )

        function_calls, text = _parse_response(response)
        if function_calls:
            # Retain the model's own Content object for the next request so the
            # functionCall part goes back with its thought_signature intact.
            model_contents.append(_model_content(response))
            first = function_calls[0]
            out: Dict[str, Any] = {
                "tool": first.name,
                "arguments": _normalize_args(first.args),
            }
            if len(function_calls) > 1:
                # The one-action-at-a-time model_fn contract can only carry a
                # single tool. Execute the first and record the rest in
                # raw_history instead of silently dropping them.
                out["deferred_tool_calls"] = [
                    {"tool": fc.name, "arguments": _normalize_args(fc.args)}
                    for fc in function_calls[1:]
                ]
            return out
        return {"final": text}

    return model_fn
