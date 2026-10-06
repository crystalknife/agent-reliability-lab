"""OmniRoute adapter for the Mini Agent Reliability Lab.

OmniRoute exposes an OpenAI-compatible /v1 chat completions endpoint, so this
adapter reuses the OpenAI-compatible implementation in `openai.py` rather than
duplicating it. Only the endpoint, key resolution, and provider identity differ.

    model_fn(messages, tool_schemas, config) -> {"tool", "arguments"} | {"final"}

Environment variables:
    OMNIROUTE_API_KEY: Optional. The local OmniRoute server does not
        authenticate; a placeholder is sent when this is unset.

No retries, no fallback providers, and no model switching: the configured model
ID is sent verbatim on every request and provider errors propagate to the agent
loop as a model_error termination. OmniRoute `auto/*` models are not used.
"""

from typing import Any, Dict, List, Union

from .openai import make_openai_model

OMNIROUTE_BASE_URL = "http://127.0.0.1:20128/v1"
DEFAULT_MODEL = "oc/deepseek-v4-flash-free"
API_KEY_ENV = "OMNIROUTE_API_KEY"
# Placeholder: the local server ignores it, but the OpenAI client requires a value.
PLACEHOLDER_API_KEY = "omniroute-local"


def make_omniroute_model(model_name: str = DEFAULT_MODEL):
    """Return a model_fn for the local OmniRoute server.

    Args:
        model_name: The OmniRoute model ID, sent verbatim. Defaults to
            "oc/deepseek-v4-flash-free".

    Returns:
        A callable that takes (messages, tool_schemas, config) and returns either
        a tool call dict or a final answer dict.
    """
    return make_openai_model(
        model_name,
        base_url=OMNIROUTE_BASE_URL,
        api_key_env=API_KEY_ENV,
        default_api_key=PLACEHOLDER_API_KEY,
    )