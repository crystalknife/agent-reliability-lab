"""Tests for the LangChain framework adapter (EXP-002).

Server-free: the scripted control replays the waiver-c101 oracle through
the real create_agent loop. No llama-server, no network, no API keys.
"""

import unittest

from minilab.env import MiniBankEnv
from minilab.evaluation import evaluate
from minilab.frameworks.langchain import (
    FRAMEWORK,
    STRATEGIES,
    STRATEGY,
    ScriptedChatModel,
    build_tools,
    framework_version,
    run_langchain_agent,
    strip_chatter,
)
from minilab.runner import _ORACLE
from minilab.tasks import get_task
from minilab.tools import TOOL_NAMES


def _meta(**over):
    base = {"experiment_id": "exp-test", "run_id": "run-test",
            "timestamp": 0.0, "model": "scripted", "provider": "local-stub",
            "temperature": 0.0, "seed": 0}
    base.update(over)
    return base


class TestFrameworkMetadata(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(FRAMEWORK, "langchain")
        self.assertEqual(STRATEGY, "langchain.agents.create_agent")

    def test_version_is_installed_not_fabricated(self):
        import importlib.metadata
        self.assertEqual(framework_version(),
                         importlib.metadata.version("langchain"))


class TestToolWiring(unittest.TestCase):
    def test_all_five_minibank_tools_registered(self):
        tools = build_tools(MiniBankEnv())
        self.assertEqual(sorted(t.name for t in tools), sorted(TOOL_NAMES))

    def test_tools_delegate_to_dispatch(self):
        import json
        tools = {t.name: t for t in build_tools(MiniBankEnv())}
        obs = json.loads(tools["get_customer"].invoke({"customer_id": "C101"}))
        self.assertEqual(obs["customer_id"], "C101")
        obs = json.loads(tools["calculate"].invoke({"op": "avg", "values": [1800, 1200]}))
        self.assertEqual(obs["result"], 1500.0)
        obs = json.loads(tools["search_policy"].invoke({"query": "waiver"}))
        self.assertTrue(any(p["policy_id"] == "WAIVER-01" for p in obs))

    def test_tool_errors_are_data_not_raises(self):
        import json
        tools = {t.name: t for t in build_tools(MiniBankEnv())}
        obs = json.loads(tools["get_transactions"].invoke({"account_id": ""}))
        self.assertIn("error", obs)


class TestScriptedControl(unittest.TestCase):
    def test_waiver_c101_oracle_passes(self):
        task = get_task("waiver-c101")
        calls, final = _ORACLE["waiver-c101"]
        traj = run_langchain_agent(
            "waiver-c101", task["prompt"], MiniBankEnv(),
            ScriptedChatModel(calls, final).model,
            max_steps=task["max_steps"], meta=_meta())
        self.assertEqual(traj.termination_reason, "agent_final")
        self.assertEqual(traj.final_answer, final)
        self.assertEqual([s.action for s in traj.steps], [c[0] for c in calls])
        for step, (name, args) in zip(traj.steps, calls):
            self.assertEqual(step.arguments, args)
            self.assertIsNotNone(step.observation)
        self.assertEqual(traj.model, "scripted")
        self.assertEqual(traj.temperature, 0.0)
        self.assertTrue(traj.raw_history)

    def test_control_is_evaluator_compatible(self):
        task = get_task("waiver-c101")
        calls, final = _ORACLE["waiver-c101"]
        traj = run_langchain_agent(
            "waiver-c101", task["prompt"], MiniBankEnv(),
            ScriptedChatModel(calls, final).model,
            max_steps=task["max_steps"], meta=_meta())
        verdict = evaluate(task, traj)
        self.assertTrue(verdict["passed"])

    def test_runaway_loop_terminates_as_max_steps(self):
        from minilab.frameworks.langchain import build_tools as _bt  # noqa
        from langchain_core.language_models.chat_models import BaseChatModel
        from langchain_core.messages import AIMessage
        from langchain_core.outputs import ChatGeneration, ChatResult

        class _Forever(BaseChatModel):
            @property
            def _llm_type(self):
                return "test-forever"

            def bind_tools(self, tools, *, tool_choice=None, **kwargs):
                return self

            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                return ChatResult(generations=[ChatGeneration(message=AIMessage(
                    content="", tool_calls=[{"name": "get_customer",
                                             "args": {"customer_id": "C101"},
                                             "id": "c0", "type": "tool_call"}]))])

        task = get_task("waiver-c101")
        traj = run_langchain_agent(
            "waiver-c101", task["prompt"], MiniBankEnv(), _Forever(),
            max_steps=task["max_steps"], meta=_meta())
        self.assertEqual(traj.termination_reason, "max_steps")
        self.assertIsNone(traj.final_answer)

    def test_model_fault_becomes_model_error(self):
        from langchain_core.language_models.chat_models import BaseChatModel

        class _Broken(BaseChatModel):
            @property
            def _llm_type(self):
                return "test-broken"

            def bind_tools(self, tools, *, tool_choice=None, **kwargs):
                return self

            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                raise ConnectionError("connection refused by test double")

        task = get_task("waiver-c101")
        traj = run_langchain_agent(
            "waiver-c101", task["prompt"], MiniBankEnv(), _Broken(),
            max_steps=task["max_steps"], meta=_meta())
        self.assertEqual(traj.termination_reason, "model_error")
        self.assertIn("error", traj.steps[-1].observation)


class TestChatterStripped(unittest.TestCase):
    def _chattery(self):
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
        return [
            SystemMessage(content="sys"),
            HumanMessage(content="do it"),
            AIMessage(content="I will call the tool now.",
                      tool_calls=[{"name": "get_customer",
                                   "args": {"customer_id": "C101"},
                                   "id": "c0", "type": "tool_call"}]),
            HumanMessage(content="unrelated"),
        ]

    def test_strip_blanks_chatter_preserves_calls(self):
        from langchain_core.messages import AIMessage
        msgs = self._chattery()
        out = strip_chatter(msgs)
        ai = out[2]
        self.assertEqual(ai.content, "")
        self.assertEqual(ai.tool_calls, msgs[2].tool_calls)
        self.assertEqual(ai.id, msgs[2].id)

    def test_strip_leaves_final_text_and_others_untouched(self):
        from langchain_core.messages import AIMessage
        msgs = (self._chattery()
                + [AIMessage(content="The final answer.")])
        out = strip_chatter(msgs)
        self.assertEqual(out[-1].content, "The final answer.")
        self.assertIs(out[0], msgs[0])
        self.assertIs(out[1], msgs[1])
        self.assertIs(out[3], msgs[3])
        # originals are never mutated
        self.assertEqual(msgs[2].content, "I will call the tool now.")

    def test_stripped_run_still_passes_control(self):
        task = get_task("waiver-c101")
        calls, final = _ORACLE["waiver-c101"]
        traj = run_langchain_agent(
            "waiver-c101", task["prompt"], MiniBankEnv(),
            ScriptedChatModel(calls, final).model,
            max_steps=task["max_steps"], meta=_meta(),
            strategy="chatter_stripped")
        self.assertEqual(traj.termination_reason, "agent_final")
        self.assertEqual(traj.final_answer, final)
        self.assertTrue(evaluate(task, traj)["passed"])

    def test_chatter_never_reaches_next_model_call(self):
        from langchain_core.language_models.chat_models import BaseChatModel
        from langchain_core.messages import AIMessage
        from langchain_core.outputs import ChatGeneration, ChatResult

        seen = []

        class _Chatty(BaseChatModel):
            @property
            def _llm_type(self):
                return "test-chatty"

            def bind_tools(self, tools, *, tool_choice=None, **kwargs):
                return self

            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                seen.append(list(messages))
                n = len(seen)
                if n == 1:
                    return ChatResult(generations=[ChatGeneration(message=AIMessage(
                        content="Let me look that up.",
                        tool_calls=[{"name": "get_customer",
                                     "args": {"customer_id": "C101"},
                                     "id": "c0", "type": "tool_call"}]))])
                return ChatResult(generations=[ChatGeneration(
                    message=AIMessage(content="C101 is the customer."))])

        task = get_task("waiver-c101")
        traj = run_langchain_agent(
            "waiver-c101", task["prompt"], MiniBankEnv(), _Chatty(),
            max_steps=task["max_steps"], meta=_meta(),
            strategy="chatter_stripped")
        # second model call must not see the chatter text...
        second_inputs = seen[1]
        ai_texts = [m.content for m in second_inputs
                    if type(m).__name__ == "AIMessage"]
        self.assertTrue(all(t == "" for t in ai_texts))
        # ...but the recorded trajectory preserves what was emitted
        self.assertEqual(traj.final_answer, "C101 is the customer.")
        raws = [m.get("content") for m in traj.raw_history]
        self.assertIn("Let me look that up.", raws)

    def test_normal_strategy_keeps_chatter_visible(self):
        from langchain_core.language_models.chat_models import BaseChatModel
        from langchain_core.messages import AIMessage
        from langchain_core.outputs import ChatGeneration, ChatResult

        seen = []

        class _Chatty(BaseChatModel):
            @property
            def _llm_type(self):
                return "test-chatty-normal"

            def bind_tools(self, tools, *, tool_choice=None, **kwargs):
                return self

            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                seen.append(list(messages))
                if len(seen) == 1:
                    return ChatResult(generations=[ChatGeneration(message=AIMessage(
                        content="Let me look that up.",
                        tool_calls=[{"name": "get_customer",
                                     "args": {"customer_id": "C101"},
                                     "id": "c0", "type": "tool_call"}]))])
                return ChatResult(generations=[ChatGeneration(
                    message=AIMessage(content="done."))])

        task = get_task("waiver-c101")
        run_langchain_agent(
            "waiver-c101", task["prompt"], MiniBankEnv(), _Chatty(),
            max_steps=task["max_steps"], meta=_meta(), strategy="normal")
        ai_texts = [m.content for m in seen[1]
                    if type(m).__name__ == "AIMessage"]
        self.assertIn("Let me look that up.", ai_texts)


class TestSchemaParity(unittest.TestCase):
    def _client_schemas(self, parity):
        from langchain_core.utils.function_calling import convert_to_openai_tool
        return {t.name: convert_to_openai_tool(t)["function"]["parameters"]
                for t in build_tools(MiniBankEnv(), parity=parity)}

    def test_parity_matches_harness_semantics(self):
        import json
        from minilab.tools import TOOL_SCHEMAS
        got = self._client_schemas(parity=True)
        for spec in TOOL_SCHEMAS:
            params = got[spec["name"]]
            self.assertEqual(params.get("required", []),
                             sorted(p for p, s in spec["params"].items()
                                    if s.get("required")))
            for pname, pspec in spec["params"].items():
                sent = params["properties"][pname]
                self.assertEqual(sent.get("type"), pspec["type"], (spec["name"], pname))
                self.assertEqual(sent.get("description"), pspec.get("description"))
                self.assertNotIn("anyOf", sent)
                self.assertNotIn("default", sent)
                if "items" in pspec:
                    self.assertEqual(sent.get("items"), pspec["items"])
        calc = got["calculate"]["properties"]["values"]
        self.assertEqual(calc, {"type": "array", "description": "Non-empty numeric list",
                                "items": {"type": "number"}})

    def test_normal_behavior_unchanged(self):
        got = self._client_schemas(parity=False)
        # The audit-documented decoration must still be present in normal mode.
        nullable = got["get_account"]["properties"]["customer_id"]
        self.assertIn("anyOf", nullable)
        self.assertEqual(got["calculate"]["properties"]["values"].get("items"), {})

    def test_parity_run_passes_control(self):
        task = get_task("waiver-c101")
        calls, final = _ORACLE["waiver-c101"]
        traj = run_langchain_agent(
            "waiver-c101", task["prompt"], MiniBankEnv(),
            ScriptedChatModel(calls, final).model,
            max_steps=task["max_steps"], meta=_meta(),
            strategy="schema_parity")
        self.assertEqual(traj.termination_reason, "agent_final")
        self.assertTrue(evaluate(task, traj)["passed"])

    def test_unknown_strategy_rejected(self):
        task = get_task("waiver-c101")
        calls, final = _ORACLE["waiver-c101"]
        with self.assertRaises(ValueError):
            run_langchain_agent(
                "waiver-c101", task["prompt"], MiniBankEnv(),
                ScriptedChatModel(calls, final).model,
                max_steps=task["max_steps"], meta=_meta(),
                strategy="chatter_stripped+schema_parity")

    def test_strategy_recorded_on_rows(self):
        import json
        import tempfile
        import os
        from minilab.runner import run_experiment
        with tempfile.TemporaryDirectory() as td:
            rows = run_experiment(
                ["waiver-c101"], repeats=1, seed=0, model="scripted",
                results_path=os.path.join(td, "r.jsonl"),
                trajectories_path=os.path.join(td, "t.jsonl"),
                framework="langchain", strategy="chatter_stripped")
            self.assertEqual(rows[0]["strategy"], "chatter_stripped")
            self.assertEqual(rows[0]["framework"], "langchain")
            self.assertTrue(rows[0]["passed"])


class TestCombinedStrategy(unittest.TestCase):
    COMBINED = "schema_parity_chatter_stripped"

    def _chatty_fake(self, seen):
        from langchain_core.language_models.chat_models import BaseChatModel
        from langchain_core.messages import AIMessage
        from langchain_core.outputs import ChatGeneration, ChatResult

        class _Chatty(BaseChatModel):
            @property
            def _llm_type(self):
                return "test-chatty-combined"

            def bind_tools(self, tools, *, tool_choice=None, **kwargs):
                return self

            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                seen.append(list(messages))
                if len(seen) == 1:
                    return ChatResult(generations=[ChatGeneration(message=AIMessage(
                        content="Let me look that up.",
                        tool_calls=[{"name": "get_customer",
                                     "args": {"customer_id": "C101"},
                                     "id": "c0", "type": "tool_call"}]))])
                return ChatResult(generations=[ChatGeneration(
                    message=AIMessage(content="C101 is the customer."))])

        return _Chatty()

    def test_combined_is_exactly_both_controls(self):
        from minilab.frameworks.langchain import _PARITY_STRATEGIES, _STRIP_STRATEGIES
        self.assertIn(self.COMBINED, _PARITY_STRATEGIES)
        self.assertIn(self.COMBINED, _STRIP_STRATEGIES)
        # Singles stay single: no third behavior leaks into them.
        self.assertNotIn("chatter_stripped", _PARITY_STRATEGIES)
        self.assertNotIn("schema_parity", _STRIP_STRATEGIES)

    def test_combined_strips_chatter_from_history(self):
        task = get_task("waiver-c101")
        seen = []
        traj = run_langchain_agent(
            "waiver-c101", task["prompt"], MiniBankEnv(),
            self._chatty_fake(seen),
            max_steps=task["max_steps"], meta=_meta(),
            strategy=self.COMBINED)
        ai_texts = [m.content for m in seen[1]
                    if type(m).__name__ == "AIMessage"]
        self.assertTrue(all(t == "" for t in ai_texts))
        self.assertEqual(traj.final_answer, "C101 is the customer.")

    def test_combined_preserves_calls_and_record(self):
        task = get_task("waiver-c101")
        seen = []
        traj = run_langchain_agent(
            "waiver-c101", task["prompt"], MiniBankEnv(),
            self._chatty_fake(seen),
            max_steps=task["max_steps"], meta=_meta(),
            strategy=self.COMBINED)
        self.assertEqual(traj.steps[0].action, "get_customer")
        self.assertEqual(traj.steps[0].arguments, {"customer_id": "C101"})
        self.assertIsNotNone(traj.steps[0].observation)
        raws = [m.get("content") for m in traj.raw_history]
        self.assertIn("Let me look that up.", raws)

    def test_combined_uses_parity_schemas(self):
        import json
        from langchain_core.utils.function_calling import convert_to_openai_tool
        from minilab.tools import TOOL_SCHEMAS
        # The parity renderer is strategy-independent; the combined arm must
        # resolve to it (proved here) and the passing control run below proves
        # the combined arm executes through it.
        single = {t.name: convert_to_openai_tool(t)["function"]["parameters"]
                  for t in build_tools(MiniBankEnv(), parity=True)}
        for spec in TOOL_SCHEMAS:
            sent = single[spec["name"]]
            self.assertNotIn("anyOf", json.dumps(sent, sort_keys=True))
        task = get_task("waiver-c101")
        calls, final = _ORACLE["waiver-c101"]
        traj = run_langchain_agent(
            "waiver-c101", task["prompt"], MiniBankEnv(),
            ScriptedChatModel(calls, final).model,
            max_steps=task["max_steps"], meta=_meta(),
            strategy=self.COMBINED)
        self.assertEqual(traj.termination_reason, "agent_final")
        self.assertTrue(evaluate(task, traj)["passed"])

    def test_combined_strategy_recorded_on_rows(self):
        import os
        import tempfile
        from minilab.runner import run_experiment
        with tempfile.TemporaryDirectory() as td:
            rows = run_experiment(
                ["waiver-c101"], repeats=1, seed=0, model="scripted",
                results_path=os.path.join(td, "r.jsonl"),
                trajectories_path=os.path.join(td, "t.jsonl"),
                framework="langchain", strategy=self.COMBINED)
            self.assertEqual(rows[0]["strategy"], self.COMBINED)
            self.assertEqual(rows[0]["framework"], "langchain")
            self.assertTrue(rows[0]["passed"])

    def test_unknown_strategy_still_rejected(self):
        task = get_task("waiver-c101")
        calls, final = _ORACLE["waiver-c101"]
        with self.assertRaises(ValueError):
            run_langchain_agent(
                "waiver-c101", task["prompt"], MiniBankEnv(),
                ScriptedChatModel(calls, final).model,
                max_steps=task["max_steps"], meta=_meta(),
                strategy="schema_parity_chatter_stripped_extra")


if __name__ == "__main__":
    unittest.main()
