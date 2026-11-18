"""One model-adapter contract for BankOps, over the OpenAI-compatible chat-completions protocol.

Standard-library HTTP only (decision D4): explicit timeouts, no hidden SDK retries, errors typed at
the HTTP boundary. One adapter serves hosted OpenAI-compatible APIs and local servers (Ollama,
vLLM, llama-server). It returns exactly the `run_agent` contract (src/minilab/agent.py:117):
{"tool", "arguments"} | {"calls", [...]} | {"final"}.

Credentials are references ("env:NAME"), resolved only when a request is sent; the value never
appears in a profile, error, health result or call record.
"""

from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from minilab.models.local import to_api_messages  # same message translation as the legacy local adapter

_ENV_REF = re.compile(r"^env:([A-Z_][A-Z0-9_]*)$")
_RETRYABLE = ("rate_limited", "server", "timeout")
MAX_BODY_SNIPPET = 200


# -- typed errors ------------------------------------------------------------
class ModelError(Exception):
    """Base error. `origin` says where the failure arose: config | provider | transport | adapter."""
    kind, origin = "error", "adapter"

    def __init__(self, message: str, *, status: Optional[int] = None, retry_after: Optional[float] = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class ConfigError(ModelError):
    kind, origin = "config", "config"


class AuthError(ModelError):
    kind, origin = "auth", "provider"


class NotFoundError(ModelError):
    kind, origin = "not_found", "provider"


class RequestRejectedError(ModelError):
    kind, origin = "rejected", "provider"


class RateLimitError(ModelError):
    kind, origin = "rate_limited", "provider"


class ServerError(ModelError):
    kind, origin = "server", "provider"


class ModelTimeoutError(ModelError):
    kind, origin = "timeout", "transport"


class TransportError(ModelError):
    kind, origin = "transport", "transport"


class MalformedResponseError(ModelError):
    kind, origin = "malformed", "provider"


class InvalidToolCallError(MalformedResponseError):
    """A well-formed response whose *model-generated* tool call is unusable (no name, arguments that
    are not a JSON object). Attributed to the model (an agent failure), not to the provider."""
    kind, origin = "invalid_tool_call", "model"


class TruncatedResponseError(InvalidToolCallError):
    """The model's output was cut off at the output-length limit (`finish_reason == "length"`).

    Verified against llama.cpp llama-server b11435: a tool call cut off this way arrives with NO
    `tool_calls` and the partial call as plain `content` (e.g. '<tool_call>\\n{"name": "'), so it must
    never be read as a deliberate final answer. Attributed to the model; usage is still recorded."""
    kind, origin = "truncated", "model"

    def __init__(self, message: str, *, partial_content: str = "", **kw):
        super().__init__(message, **kw)
        self.partial_content = partial_content


def _error_for_status(status: int) -> type:
    if status in (401, 403):
        return AuthError
    if status == 404:
        return NotFoundError
    if status == 429:
        return RateLimitError
    if status >= 500:
        return ServerError
    return RequestRejectedError


# -- profiles and credentials ---------------------------------------------------
@dataclass(frozen=True)
class ModelProfile:
    id: str
    kind: str                      # "api" | "local"
    base_url: str                  # OpenAI-compatible root, e.g. https://host/v1
    model_id: str                  # always explicit; no built-in catalogue
    credential_ref: Optional[str] = None   # "env:NAME" or None (local servers only)
    timeout_s: float = 60.0
    max_retries: int = 0

    def __post_init__(self):
        if self.kind not in ("api", "local"):
            raise ConfigError(f"profile {self.id!r}: kind must be 'api' or 'local'")
        if not re.match(r"^https?://[^\s]+$", self.base_url or ""):
            raise ConfigError(f"profile {self.id!r}: base_url must be an http(s) URL")
        if not self.model_id:
            raise ConfigError(f"profile {self.id!r}: model_id is required")
        if self.credential_ref is not None and not _ENV_REF.match(self.credential_ref):
            # never echo the value: it may be a pasted secret
            raise ConfigError(f"profile {self.id!r}: credential_ref must be 'env:NAME' (a reference, not a secret)")
        if self.kind == "api" and self.credential_ref is None:
            raise ConfigError(f"profile {self.id!r}: an api profile needs a credential_ref")
        if not (0 < self.timeout_s <= 600):
            raise ConfigError(f"profile {self.id!r}: timeout_s must be in (0, 600]")
        if not (isinstance(self.max_retries, int) and 0 <= self.max_retries <= 5):
            raise ConfigError(f"profile {self.id!r}: max_retries must be an int in [0, 5]")


def api_profile(model_id: str, *, base_url: str = "https://openrouter.ai/api/v1",
                credential_ref: str = "env:OPENROUTER_API_KEY", **kw) -> ModelProfile:
    """Hosted OpenAI-compatible API profile (OpenRouter by default; any compatible base_url works)."""
    return ModelProfile(id=kw.pop("id", f"api:{model_id}"), kind="api", base_url=base_url,
                        model_id=model_id, credential_ref=credential_ref, **kw)


def local_profile(model_id: str, *, base_url: str = "http://127.0.0.1:11434/v1", **kw) -> ModelProfile:
    """Local inference profile. Default is Ollama; vLLM and llama-server differ only in base_url."""
    return ModelProfile(id=kw.pop("id", f"local:{model_id}"), kind="local", base_url=base_url,
                        model_id=model_id, **kw)


def resolve_credential(ref: Optional[str]) -> Optional[str]:
    if ref is None:
        return None
    m = _ENV_REF.match(ref)
    if not m:
        raise ConfigError("credential_ref must be 'env:NAME'")
    value = os.environ.get(m.group(1))
    if not value:
        raise ConfigError(f"credential {ref} is not set in the environment")
    return value


# -- tool-schema translation (same shape as the legacy local adapter) ------------------
def to_api_tools(tool_schemas: List[Dict]) -> List[Dict]:
    tools = []
    for tool in tool_schemas:
        properties: Dict[str, Any] = {}
        required: List[str] = []
        for name, spec in tool.get("params", {}).items():
            prop: Dict[str, Any] = {"type": spec.get("type", "string")}
            for key in ("description", "items"):
                if key in spec:
                    prop[key] = spec[key]
            properties[name] = prop
            if spec.get("required"):
                required.append(name)
        tools.append({"type": "function", "function": {
            "name": tool["name"], "description": tool.get("description", ""),
            "parameters": {"type": "object", "properties": properties, "required": required}}})
    return tools


# -- adapter ---------------------------------------------------------------------------
class OpenAICompatibleAdapter:
    """ModelAdapter for any OpenAI-compatible /chat/completions endpoint."""

    def __init__(self, profile: ModelProfile, *, sleep: Callable[[float], None] = time.sleep):
        self.profile = profile
        self._sleep = sleep
        self.last_call: Dict[str, Any] = {}

    def __repr__(self):
        return f"OpenAICompatibleAdapter(profile={self.profile.id!r}, model={self.profile.model_id!r})"

    def _post(self, body: Dict[str, Any]) -> Dict[str, Any]:
        secret = resolve_credential(self.profile.credential_ref)
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
        req = urllib.request.Request(self.profile.base_url.rstrip("/") + "/chat/completions",
                                     data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")

        def snippet(raw: bytes) -> str:
            text = raw[:MAX_BODY_SNIPPET].decode("utf-8", "replace")
            return text.replace(secret, "***") if secret else text

        try:
            with urllib.request.urlopen(req, timeout=self.profile.timeout_s) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            retry_after = None
            ra = e.headers.get("Retry-After") if e.headers else None
            if ra and ra.strip().isdigit():
                retry_after = float(ra.strip())
            cls = _error_for_status(e.code)
            try:
                detail = snippet(e.read())
            except OSError:
                detail = ""
            raise cls(f"HTTP {e.code} from {self.profile.id}: {detail}", status=e.code,
                      retry_after=retry_after) from None
        except urllib.error.URLError as e:
            if isinstance(e.reason, (socket.timeout, TimeoutError)):
                raise ModelTimeoutError(f"timed out after {self.profile.timeout_s}s ({self.profile.id})") from None
            raise TransportError(f"transport failure ({self.profile.id}): {type(e.reason).__name__}") from None
        except (socket.timeout, TimeoutError):
            raise ModelTimeoutError(f"timed out after {self.profile.timeout_s}s ({self.profile.id})") from None
        except OSError as e:  # connection reset, remote disconnect while reading
            raise TransportError(f"transport failure ({self.profile.id}): {type(e).__name__}") from None
        try:
            data = json.loads(raw)
        except ValueError:
            raise MalformedResponseError(f"non-JSON response from {self.profile.id}: {snippet(raw)!r}") from None
        if not isinstance(data, dict):
            raise MalformedResponseError(f"response from {self.profile.id} is not a JSON object")
        return data

    def _request(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """POST with the profile's retry policy (retries only rate limits, 5xx and timeouts)."""
        attempt = 0
        while True:
            start = time.monotonic()
            try:
                data = self._post(body)
                self.last_call = {"latency_ms": round((time.monotonic() - start) * 1000, 1),
                                  "attempts": attempt + 1, "usage": data.get("usage")}
                return data
            except ModelError as e:
                if e.kind not in _RETRYABLE or attempt >= self.profile.max_retries:
                    self.last_call = {"attempts": attempt + 1, "error_kind": e.kind, "status": e.status}
                    raise
                delay = e.retry_after if e.retry_after is not None else min(8.0, 0.5 * 2 ** attempt)
                self._sleep(delay)
                attempt += 1

    def complete(self, messages: List[Dict], tool_schemas: List[Dict], config: Dict) -> Dict[str, Any]:
        """config: temperature (default 0.0); optional max_tokens (per-response output cap) and seed
        (sampling seed). Optional fields are sent only when given, so earlier request bodies are unchanged."""
        body: Dict[str, Any] = {"model": self.profile.model_id, "messages": to_api_messages(messages),
                                "temperature": config.get("temperature", 0.0)}
        for key in ("max_tokens", "seed"):
            if config.get(key) is not None:
                body[key] = config[key]
        if tool_schemas:
            body["tools"] = to_api_tools(tool_schemas)
            body["tool_choice"] = "auto"
        data = self._request(body)
        try:
            choice = data["choices"][0]
            message = choice["message"]
            if not isinstance(message, dict):
                raise TypeError
        except (KeyError, IndexError, TypeError):
            raise MalformedResponseError(f"response from {self.profile.id} has no choices[0].message") from None
        finish_reason = choice.get("finish_reason")
        visible = message.get("content")
        # Model-visible assistant text (also the text that accompanies tool calls). A separate
        # `reasoning_content` field (hidden chain-of-thought on some servers) is never recorded; only
        # the fact that one was present and withheld.
        self.last_call.update(finish_reason=finish_reason, system_fingerprint=data.get("system_fingerprint"),
                              assistant_text=visible if isinstance(visible, str) and visible else None,
                              reasoning_content_withheld=bool(message.get("reasoning_content")))
        if finish_reason == "length":
            content = message.get("content")
            partial = content if isinstance(content, str) else ""
            raise TruncatedResponseError(
                f"output from {self.profile.id} stopped at the length limit ({len(partial)} characters of "
                f"partial content; tool_calls {'present' if message.get('tool_calls') else 'absent'})",
                partial_content=partial[:2000])
        calls = message.get("tool_calls") or []
        if calls:
            parsed = []
            for call in calls:
                try:
                    fn = call["function"]
                    args = json.loads(fn.get("arguments") or "{}")
                    if not isinstance(args, dict):
                        raise ValueError
                    parsed.append({"tool": fn["name"], "arguments": args})
                except (KeyError, TypeError, ValueError):
                    raise InvalidToolCallError(f"invalid tool call from {self.profile.id}") from None
            return parsed[0] if len(parsed) == 1 else {"calls": parsed}
        content = message.get("content")
        return {"final": content if isinstance(content, str) else ""}

    def as_model_fn(self):
        """The callable `run_agent` expects: model_fn(messages, tool_schemas, config)."""
        return self.complete


def health_check(profile: ModelProfile, *, adapter_cls=OpenAICompatibleAdapter) -> Dict[str, Any]:
    """One minimal request. Never raises; returns {ok, kind, latency_ms, status, profile}."""
    start = time.monotonic()
    out: Dict[str, Any] = {"profile": profile.id, "ok": False, "kind": None, "status": None}
    try:
        adapter = adapter_cls(profile)
        adapter._request({"model": profile.model_id, "max_tokens": 1,
                          "messages": [{"role": "user", "content": "ping"}]})
        out.update(ok=True, kind="ok")
    except ModelError as e:
        out.update(kind=e.kind, status=e.status)
    out["latency_ms"] = round((time.monotonic() - start) * 1000, 1)
    return out
