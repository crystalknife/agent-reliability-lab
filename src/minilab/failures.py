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
