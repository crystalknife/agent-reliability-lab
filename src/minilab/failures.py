"""Failure taxonomy. Rule-based, no LLM judge.

Two independent axes:

* TRIAL_STATUSES says whether a trial produced usable evidence at all. A
  provider/API fault (HTTP 429/401/403/404/5xx, connection failure, timeout)
  is not an agent behavior, so it is recorded here and excluded from agent
  reliability rather than being charged to the agent.
* FAILURES is the agent's own failure taxonomy and is applied only to valid
  trials. It is unchanged.

A provider_error deliberately maps to "none" in FAILURES: there is no agent
failure to attribute. The trial status carries the distinction.
"""

import re

FAILURES = (
    "none",
    "tool_misuse",
    "evidence_gap",
    "policy_misread",
    "arithmetic_error",
    "premature_stop",
    "format_violation",
)

TRIAL_STATUSES = (
    "valid",
    "provider_error",
    "environment_error",
    "evaluator_error",
)


def _steps_of(trajectory):
    if isinstance(trajectory, dict):
        return trajectory.get("steps", [])
    return trajectory.steps or []


def _termination_of(trajectory):
    if isinstance(trajectory, dict):
        return trajectory.get("termination_reason", "")
    return trajectory.termination_reason


def _as_dict(s):
    return s if isinstance(s, dict) else {"action": s.action, "observation": s.observation}


def _error_text(trajectory):
    """Concatenated error strings recorded in step observations."""
    out = []
    for s in _steps_of(trajectory):
        d = _as_dict(s)
        obs = d.get("observation")
        if isinstance(obs, dict) and "error" in obs:
            out.append(str(obs["error"]))
    return "\n".join(out)


# Provider/API fault markers, matched against the recorded error text. The raw
# provider message is never rewritten, so these only gate classification.
#
# Gemini's google-genai SDK surfaces every API fault as a bare ClientError whose
# repr carries the HTTP status and status string but no exception class name,
# so status strings and codes are matched explicitly.
_PROVIDER_PATTERNS = (
    # SDK exception class names (repr'd into the observation by the agent loop)
    "ratelimiterror", "authenticationerror", "permissiondeniederror",
    "notfounderror", "internalservererror", "badgatewayerror",
    "serviceunavailableerror", "overloadederror", "apierror", "apistatus",
    "apiconnectionerror", "apitimeouterror", "timeouterror",
    # Gemini status strings and codes
    "resource_exhausted", "error code: 429", "429 resource_exhausted",
    "error code: 401", "error code: 403", "error code: 404",
    "error code: 500", "error code: 502", "error code: 503",
    # OpenAI-compatible provider messages
    "model is unavailable", "model not found", "invalid_api_key",
    "insufficient_quota", "permission denied", "unauthorized",
    "rate limit", "server disconnected", "remote end closed",
    "connection refused", "connection reset", "connection aborted",
    "connection error", "timed out", "timeout",
)

_HTTP_CODE_RE = re.compile(r"error code:\s*(\d{3})\b")


def provider_error_text(trajectory):
    """Raw provider error string(s), preserved verbatim for debugging."""
    return _error_text(trajectory)


def is_provider_error(trajectory):
    """True when the trial failed for provider/API reasons, not agent behavior.

    The agent loop only sets model_error when the model call raised, so that
    termination is a provider fault on its own. A non-model termination is
    checked against known provider markers, so an HTTP fault surfacing on a
    tool_error step is still caught.
    """
    term = _termination_of(trajectory)
    text = _error_text(trajectory).lower()
    if not text:
        return False
    if term == "model_error":
        return True
    if any(p in text for p in _PROVIDER_PATTERNS):
        return True
    code = _HTTP_CODE_RE.search(text)
    return bool(code) and (code.group(1)[0] == "4" or code.group(1)[0] == "5")


def classify_trial(trajectory, evaluator_raised=False):
    """Return one of TRIAL_STATUSES.

    provider_error: HTTP 429/401/403/404/5xx, connection failure, timeout, or
        any other model/API fault. Raw provider text stays on the trajectory.
    environment_error: the local environment could not answer (data missing,
        env construction fault).
    evaluator_error: the deterministic evaluator itself failed.
    valid: the trial completed and its failure, if any, is the agent's.
    """
    if evaluator_raised:
        return "evaluator_error"
    if is_provider_error(trajectory):
        return "provider_error"
    return "valid"


# ---------------------------------------------------------------------------
# Fault origin (2026-11-16). A NON-SCORING annotation: it never changes
# trial_status, the agent taxonomy or any reliability number.
#
# It reports three separate things and never merges them:
#   origin       WHAT was observed at the exception boundary
#   attribution  whether that observation establishes WHOSE fault it was
#                ("established") or not ("unattributed")
#   basis        how the observation was made (typed record vs message text)
#
# Deliberately conservative: an exception class from a provider SDK does not
# prove the provider was at fault (a 401 is a credential/configuration
# matter; a 400 may be a request the local adapter built wrongly; a timeout
# may be the local network or a local model server). Only "the service
# answered with a server-side error status" is treated as established, and
# even then the service may be a local one.
# ---------------------------------------------------------------------------
FAULT_ORIGINS = (
    "none",
    # model-call boundary
    "model_service_error",          # the model service answered 429 or 5xx (status read from the exception object)
    "request_rejected",             # the service answered 4xx: credentials, configuration, model name or the adapter's request
    "transport",                    # no response: connection or timeout (local network, local server or remote)
    "sdk_unattributed",             # an SDK exception with no status and no transport class (e.g. SDK-side validation/configuration)
    "model_call_local",             # a non-SDK exception: local adapter logic, or adapter parsing of malformed model output
    # output-validation boundary
    "model_output_format",          # the model's response had no valid call/final shape
    # tool-dispatch boundary
    "agent_unknown_tool",           # the agent named a tool that does not exist
    "agent_invalid_arguments",      # the tool raised and the arguments violate the tool's declared schema
    "tool_or_environment",          # the tool raised although the arguments satisfy the declared schema
    "tool_execution_unattributed",  # the tool raised and schema conformance could not be determined
    # evaluator boundary
    "evaluator",
    # no typed record (historical trajectories, LangChain path)
    "provider_suspected",           # the legacy message-text rule matched a provider pattern
    "model_call_unattributed",      # a model_error with no typed record and no provider pattern
)
FAULT_ATTRIBUTIONS = ("established", "unattributed", "none")
FAULT_BASES = ("typed_boundary_and_class", "typed_boundary_only", "text_heuristic", "none")

# Top-level modules of provider SDKs and HTTP stacks used by the adapters.
_SDK_ROOTS = ("openai", "httpx", "httpcore", "google", "requests", "urllib3", "aiohttp", "grpc",
              "langchain_openai", "langchain_core", "pydantic", "pydantic_core")
# Class names (the class itself or any base) that mean "no response arrived".
_TRANSPORT_NAMES = frozenset((
    "ConnectionError", "ConnectionRefusedError", "ConnectionResetError", "ConnectionAbortedError",
    "BrokenPipeError", "TimeoutError", "timeout", "gaierror", "herror", "SSLError", "URLError",
    "RemoteDisconnected", "IncompleteRead",
    "APIConnectionError", "APITimeoutError", "ConnectError", "ConnectTimeout", "ReadTimeout",
    "WriteTimeout", "PoolTimeout", "TimeoutException", "NetworkError", "TransportError",
    "ReadError", "WriteError", "RemoteProtocolError",
))


def _typed_fault(trajectory):
    """The first typed fault record in the trajectory, or None (historical
    trajectories and the LangChain path carry none)."""
    for s in _steps_of(trajectory):
        obs = _as_dict(s).get("observation")
        if isinstance(obs, dict) and isinstance(obs.get("fault"), dict):
            f = obs["fault"]
            if isinstance(f.get("boundary"), str):
                return f
    return None


def fault_origin(trajectory, evaluator_raised=False, evaluator_exception=None):
    """Return {"origin", "attribution", "basis", "boundary", "exception_type",
    "http_status"}. See the block comment above for what each means.

    Historical trajectories and adapters/frameworks that record no typed
    fault fall back to the legacy message-text rule and are marked
    basis="text_heuristic", attribution="unattributed".
    """
    def out(origin, attribution, basis, fault=None):
        fault = fault or {}
        return {"origin": origin, "attribution": attribution, "basis": basis,
                "boundary": fault.get("boundary"), "exception_type": fault.get("exception_type"),
                "http_status": fault.get("http_status")}

    if evaluator_raised:
        if evaluator_exception is not None:
            return out("evaluator", "established", "typed_boundary_and_class",
                       {"boundary": "evaluator", "exception_type": type(evaluator_exception).__name__})
        return out("evaluator", "established", "typed_boundary_only", {"boundary": "evaluator"})

    fault = _typed_fault(trajectory)
    if fault is not None:
        boundary = fault["boundary"]
        has_class = isinstance(fault.get("exception_type"), str)
        basis = "typed_boundary_and_class" if has_class else "typed_boundary_only"
        if boundary == "model_output_validation":
            return out("model_output_format", "established", "typed_boundary_only", fault)
        if boundary == "model_call":
            names = {fault.get("exception_type")} | set(fault.get("exception_bases") or [])
            module_root = str(fault.get("exception_module") or "").split(".")[0]
            # D2: a status-like attribute is trusted only on an SDK/HTTP-stack exception class
            status = fault.get("http_status") if module_root in _SDK_ROOTS else None
            fault = {**fault, "http_status": status}
            if isinstance(status, int) and (status == 429 or 500 <= status <= 599):
                return out("model_service_error", "established", basis, fault)
            if isinstance(status, int) and 400 <= status <= 499:
                return out("request_rejected", "unattributed", basis, fault)
            if names & _TRANSPORT_NAMES:
                return out("transport", "unattributed", basis, fault)
            if module_root in _SDK_ROOTS:
                return out("sdk_unattributed", "unattributed", basis, fault)
            return out("model_call_local", "unattributed", basis, fault)
        if boundary == "tool_dispatch":
            if fault.get("tool_known") is False:
                return out("agent_unknown_tool", "established", basis, fault)
            valid = fault.get("arguments_schema_valid")
            if valid is False:
                return out("agent_invalid_arguments", "established", basis, fault)
            if valid is True:
                return out("tool_or_environment", "unattributed", basis, fault)
            return out("tool_execution_unattributed", "unattributed", basis, fault)
        return out("tool_execution_unattributed", "unattributed", basis, fault)

    text = _error_text(trajectory).lower()
    if text and (any(p in text for p in _PROVIDER_PATTERNS) or _HTTP_CODE_RE.search(text)):
        return out("provider_suspected", "unattributed", "text_heuristic")
    if text and _termination_of(trajectory) == "model_error":
        return out("model_call_unattributed", "unattributed", "text_heuristic")
    return out("none", "none", "none")


def classify(task, trajectory, eval_result):
    """Return one of FAILURES. eval_result is the dict from evaluation.evaluate.

    Only valid trials are classified. A provider_error is returned as "none":
    a rate limit or auth failure is not an agent failure and must not be
    counted as one in reliability metrics.
    """
    if is_provider_error(trajectory):
        return "none"
    if eval_result.get("passed"):
        return "none"
    term = _termination_of(trajectory)
    if term == "max_steps":
        return "premature_stop"
    if term == "invalid_action":
        return "format_violation"
    if term == "tool_error":
        return "tool_misuse"
    if term == "model_error":
        # A model_error that is not a recognized provider fault: the provider
        # raised something unclassifiable. Still not a tool failure.
        return "none"
    steps = [_as_dict(s) for s in _steps_of(trajectory)]
    if any(
        s.get("action") == "calculate"
        and isinstance(s.get("observation"), dict)
        and "error" in s["observation"]
        for s in steps
    ):
        return "arithmetic_error"
    if not eval_result.get("verdict_match", False):
        return "policy_misread"
    if not eval_result.get("evidence_match", False):
        return "evidence_gap"
    return "format_violation"
