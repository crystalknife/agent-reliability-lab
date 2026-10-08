"""LangChain framework adapter for MiniLab (EXP-002).

Scope: LangChain replaces ONLY the agent orchestration loop. Everything else
stays MiniLab-authoritative: the same MiniBank env/tools (via thin wrappers),
the same task prompts and step budgets, the same system prompt, and the same
canonical Trajectory schema read by the existing deterministic evaluator.

Strategy: a single LangChain 1.x tool-calling agent built with
``langchain.agents.create_agent`` (model + tools nodes, no memory, no
checkpointer, no middleware). No LangGraph code is written by hand, no
LangSmith, no retries, no fallbacks.

Budget mapping: the custom harness caps MODEL CALLS at max_steps. One graph
iteration costs ~2 recursion units (model node + tools node), so the graph
runs with ``recursion_limit = 2 * max_steps + 2``. The +2 lets a final answer
produced on exactly the max_steps-th model call terminate normally, mirroring
run_agent's inclusive budget; a runaway agent trips GraphRecursionError one
iteration later and is classified max_steps either way.

Known framework-behavior difference (documented, not worked around): the
custom harness terminates immediately on an unknown tool (tool_error ->
tool_misuse). LangChain's ToolNode instead returns an error observation and
continues the loop, so a hallucinated tool name surfaces downstream
(e.g. evidence_gap) rather than as tool_misuse. This only triggers when the
model invents a tool outside the fixed five.
"""

import hashlib
import importlib.metadata
import json

from ..models.local import LOCAL_BASE_URL, PLACEHOLDER_API_KEY
from ..tools import TOOL_SCHEMAS, build_system_prompt, dispatch
from ..trajectory import Step, Trajectory

FRAMEWORK = "langchain"
STRATEGY = "langchain.agents.create_agent"

# Isolation strategies for the EXP-002 confounder controls. Exactly one may
# be active per run. schema_parity_chatter_stripped is pure composition of
# the two controls (parity tools + chatter middleware) with no third behavior;
# anything else unknown is rejected so a new combination can only exist as an
# explicit future experiment, never an accident.
STRATEGIES = ("normal", "chatter_stripped", "schema_parity",
              "schema_parity_chatter_stripped")
_PARITY_STRATEGIES = ("schema_parity", "schema_parity_chatter_stripped")
_STRIP_STRATEGIES = ("chatter_stripped", "schema_parity_chatter_stripped")


def framework_version():
    """Actual installed langchain version. Never fabricated."""
    try:
        return importlib.metadata.version("langchain")
    except importlib.metadata.PackageNotFoundError as e:
        raise ValueError(
            "The langchain package is not installed. "
            "Install it with `pip install -e .[langchain]`."
        ) from e


def _args_schema(tool_spec):
    """Build a pydantic args model straight from the canonical tool schema."""
    from pydantic import Field, create_model

    type_map = {"string": str, "integer": int, "number": float, "array": list}
    fields = {}
    for pname, pspec in tool_spec.get("params", {}).items():
        py_type = type_map.get(pspec.get("type", "string"), str)
        if pspec.get("required"):
            fields[pname] = (py_type, Field(description=pspec.get("description", "")))
        else:
            fields[pname] = (py_type | None,
                             Field(default=None, description=pspec.get("description", "")))
    return create_model(tool_spec["name"] + "_args", **fields)


def _parity_schema_dict(tool_spec):
    """Render the canonical spec as a plain OpenAI parameters dict.

    Mirrors the parameters rendering the custom harness sends (see the
    _convert_tools helper inside models.local.make_local_model): bare JSON
    types, exact required lists, no anyOf/null decoration, no defaults, and
    items preserved. StructuredTool uses a dict args_schema verbatim for the
    model-facing schema, while runtime validation stays a raw passthrough to
    dispatch -- exactly like the harness, which never validates either.
    """
    properties = {}
    required = []
    for pname, pspec in tool_spec.get("params", {}).items():
        prop = {"type": pspec.get("type", "string")}
        if "description" in pspec:
            prop["description"] = pspec["description"]
        if "items" in pspec:
            prop["items"] = pspec["items"]
        properties[pname] = prop
        if pspec.get("required"):
            required.append(pname)
    return {"type": "object", "properties": properties, "required": required}


def build_tools(env, parity=False):
    """Wrap MiniLab dispatch as LangChain tools. Thin delegation only.

    Each wrapper drops None-valued optional args and JSON-serializes the
    observation, so ToolMessage content round-trips back into canonical form.
    No business logic lives here; env/tool semantics are untouched.
    """
    from langchain_core.tools import StructuredTool

    tools = []
    for spec in TOOL_SCHEMAS:
        name = spec["name"]

        def _run(env=env, name=name, **kwargs):
            args = {k: v for k, v in kwargs.items() if v is not None}
            return json.dumps(dispatch(env, name, args), default=str)

        _run.__name__ = "minilab_" + name
        tools.append(StructuredTool.from_function(
            func=_run,
            name=name,
            description=spec.get("description", ""),
            args_schema=(_parity_schema_dict(spec) if parity
                         else _args_schema(spec)),
        ))
    return tools


def strip_chatter(messages):
    """Blank assistant text on tool-calling messages; nothing else changes.

    Pure function and the entire Control A mechanism: the returned list is
    what the NEXT model call sees, so chatter never re-enters context (as in
    the custom harness, which never feeds it back). Tool calls, observations,
    ids, and message objects without chatter pass through untouched, and the
    agent's stored state keeps the originals for the trajectory record.
    """
    from langchain_core.messages import AIMessage

    out = []
    for m in messages:
        if (isinstance(m, AIMessage) and m.tool_calls
                and isinstance(m.content, str) and m.content.strip()):
            out.append(m.model_copy(update={"content": ""}))
        else:
            out.append(m)
    return out


class ChatterStripMiddleware:
    """Control A: mirror the harness by hiding chatter from future turns.

    Implemented as LangChain middleware wrapping each model call: only the
    request copy is stripped, so the recorded trajectory still preserves what
    the model actually emitted. Tool execution, observations, parallelism,
    and termination behavior are completely unchanged.
    """

    def __init__(self):
        from langchain.agents.middleware import AgentMiddleware

        class _Strip(AgentMiddleware):
            def wrap_model_call(self, request, handler):
                request.messages = strip_chatter(request.messages)
                return handler(request)

        self._middleware = _Strip()

    @property
    def middleware(self):
        return self._middleware


def make_langchain_chat(model_name="qwen3-4b", temperature=0.0, api_key=None):
    """ChatOpenAI pointed at the external local llama-server. No retries.

    The server is external and never started here; model_name is sent verbatim
    (llama-server ignores it, single model loaded). Server faults propagate as
    model_error, classified provider_error downstream, never agent failures.
    """
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as e:
        raise ValueError(
            "The langchain-openai package is not installed. "
            "Install it with `pip install -e .[langchain]`."
        ) from e
    import os

    return ChatOpenAI(
        model=model_name,
        base_url=LOCAL_BASE_URL,
        api_key=api_key or os.environ.get("LOCAL_API_KEY") or PLACEHOLDER_API_KEY,
        temperature=temperature,
    )


class ScriptedChatModel:
    """Deterministic control: replays the oracle tool sequence, then the final.

    A scripted control for the LangChain loop. Takes the same (calls, final)
    oracle the custom harness uses (passed in by the caller, single source of
    truth in runner._ORACLE). Implements bind_tools because create_agent
    requires it; bound schemas are recorded and ignored since the canned plan
    already encodes the correct calls.
    """

    def __init__(self, calls, final):
        self._calls = [(name, dict(args)) for name, args in calls]
        self._final = final
        self._model = self._build()

    def _build(self):
        from langchain_core.callbacks import CallbackManagerForLLMRun
        from langchain_core.language_models.chat_models import BaseChatModel
        from langchain_core.messages import AIMessage, BaseMessage
        from langchain_core.outputs import ChatGeneration, ChatResult
        from typing import Any, List, Optional

        calls, final = self._calls, self._final
        state = {"i": 0}

        class _Scripted(BaseChatModel):
            @property
            def _llm_type(self):
                return "minilab-scripted"

            def bind_tools(self, tools, *, tool_choice=None, **kwargs):
                return self

            def _generate(
                self,
                messages: List[BaseMessage],
                stop: Optional[List[str]] = None,
                run_manager: Optional[CallbackManagerForLLMRun] = None,
                **kwargs: Any,
            ) -> ChatResult:
                i = state["i"]
                state["i"] += 1
                if i < len(calls):
                    name, args = calls[i]
                    msg = AIMessage(content="", tool_calls=[{
                        "name": name, "args": args,
                        "id": f"call_{i}", "type": "tool_call",
                    }])
                else:
                    msg = AIMessage(content=final)
                return ChatResult(generations=[ChatGeneration(message=msg)])

        return _Scripted()

    @property
    def model(self):
        return self._model


def _prompt_hash(prompt):
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _message_dict(m):
    """Framework-specific raw history: LangChain message -> plain dict."""
    d = {"type": type(m).__name__}
    for key in ("content", "tool_calls", "tool_call_id", "name", "status"):
        if hasattr(m, key):
            try:
                d[key] = getattr(m, key)
            except Exception:  # noqa: BLE001 - debug context only
                pass
    return d


def run_langchain_agent(task_id, task_prompt, env, chat_model, max_steps=8,
                        meta=None, strategy="normal"):
    """Run one episode through the LangChain agent loop. Never raises.

    Same inputs, same system prompt, same per-task step budget, same canonical
    Trajectory output as the custom harness. Mirrors agent.run_agent's
    hardening contract: model faults become model_error, over-budget runs
    become max_steps, and a Trajectory is always returned.

    strategy selects one isolation control: "normal" (EXP-002 behavior),
    "chatter_stripped" (EXP-002A), "schema_parity" (EXP-002B), or
    "schema_parity_chatter_stripped" (EXP-002C: exactly both, nothing more).
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    try:
        from langgraph.errors import GraphRecursionError
    except ImportError:
        GraphRecursionError = None

    meta = meta or {}
    if strategy not in STRATEGIES:
        raise ValueError(
            f"unknown strategy: {strategy!r} (use one of {STRATEGIES}).")
    messages_in = [
        SystemMessage(content=build_system_prompt()),
        HumanMessage(content=task_prompt),
    ]
    steps = []
    final_answer = None
    overflow = False

    def _record_model_error(exc):
        steps.append(Step(step=len(steps) + 1, action="model_error", arguments={},
                          observation={"error": f"model error: {exc!r}"}))

    try:
        from langchain.agents import create_agent

        middleware = ([ChatterStripMiddleware().middleware]
                      if strategy in _STRIP_STRATEGIES else [])
        agent = create_agent(
            chat_model,
            tools=build_tools(env, parity=(strategy in _PARITY_STRATEGIES)),
            middleware=middleware,
        )
        # See module docstring: 2 recursion units per iteration + 2 so a
        # final produced on exactly the max_steps-th call still terminates.
        result = agent.invoke(
            {"messages": list(messages_in)},
            config={"recursion_limit": 2 * max_steps + 2},
        )
        out_messages = result.get("messages", [])
    except Exception as e:  # noqa: BLE001 - mirrors agent.run_agent hardening
        out_messages = []
        if GraphRecursionError is not None and isinstance(e, GraphRecursionError):
            overflow = True
        else:
            _record_model_error(e)
            return Trajectory(
                task_id=task_id, steps=steps, final_answer=None,
                termination_reason="model_error",
                raw_history=[], experiment_id=meta.get("experiment_id", ""),
                run_id=meta.get("run_id", ""), timestamp=meta.get("timestamp", ""),
                model=meta.get("model", ""), provider=meta.get("provider", ""),
                model_version=meta.get("model_version"),
                temperature=meta.get("temperature", 0.0), seed=meta.get("seed", 0),
                max_steps=max_steps, prompt=task_prompt,
                prompt_hash=_prompt_hash(task_prompt),
            )

    raw_history = [_message_dict(m) for m in out_messages]
    open_steps = {}
    for m in out_messages:
        mtype = type(m).__name__
        if mtype in ("SystemMessage", "HumanMessage"):
            continue  # loop inputs, echoed back; never agent output
        if mtype == "AIMessage":
            if m.tool_calls:
                for call in m.tool_calls:
                    step = Step(step=len(steps) + 1, action=call.get("name", ""),
                                arguments=dict(call.get("args", {}) or {}),
                                observation=None)
                    steps.append(step)
                    if call.get("id"):
                        open_steps[call["id"]] = step
            elif isinstance(m.content, str) and m.content.strip():
                final_answer = m.content
        elif mtype == "ToolMessage":
            obs = m.content
            try:
                obs = json.loads(obs) if isinstance(obs, str) else obs
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
            target = open_steps.pop(m.tool_call_id, None) if m.tool_call_id else None
            if target is None:
                for s in reversed(steps):
                    if s.observation is None:
                        target = s
                        break
            if target is not None:
                target.observation = obs
        # Any other message type carries no canonical content; it stays
        # available in raw_history for debugging.

    if overflow:
        termination_reason = "max_steps"
    else:
        # The graph returned, so the model stopped calling tools on its own:
        # the last text-only reply (possibly empty) is the final answer.
        termination_reason = "agent_final"

    return Trajectory(
        task_id=task_id,
        steps=steps,
        final_answer=final_answer,
        termination_reason=termination_reason,
        raw_history=raw_history,
        experiment_id=meta.get("experiment_id", ""),
        run_id=meta.get("run_id", ""),
        timestamp=meta.get("timestamp", ""),
        model=meta.get("model", ""),
        provider=meta.get("provider", ""),
        model_version=meta.get("model_version"),
        temperature=meta.get("temperature", 0.0),
        seed=meta.get("seed", 0),
        max_steps=max_steps,
        prompt=task_prompt,
        prompt_hash=_prompt_hash(task_prompt),
    )
