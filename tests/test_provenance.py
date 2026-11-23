"""Provenance accounting on SYNTHETIC trajectories.

Trajectories are produced by the real agent loop driven by scripted model
functions written in this file (no provider, no scoring), or are hand-built
dicts. None is experiment evidence.
"""

import copy
import unittest

from minilab.agent import run_agent
from minilab.env import MiniBankEnv
from minilab.provenance import (
    ADAPTER_CAPABILITIES,
    DISCARD_ADAPTER_DEFERRED,
    DISCARD_AFTER_TERMINAL_ERROR,
    capture_completeness_for,
    DISCARD_SEQUENTIAL_EXTRA,
    DISCARD_UNCLASSIFIED,
    INDETERMINATE,
    NOT_OBSERVABLE,
    VERIFIED,
    IterationCapPolicy,
    NoBudgetPolicy,
    Observed,
    analyze_trial,
)


def scripted(responses):
    it = iter(responses)

    def fn(messages, tools, config):
        out = next(it)
        if isinstance(out, Exception):
            raise out
        return out

    return fn


def call(tool, **arguments):
    return {"tool": tool, "arguments": arguments}


def run(responses, exec_mode="sequential", max_steps=8):
    traj = run_agent("synthetic-provenance", "SYNTHETIC prompt mentioning C101", MiniBankEnv(),
                     scripted(responses), max_steps=max_steps,
                     meta={"run_id": "run-1", "experiment_id": "exp-synth"}, exec_mode=exec_mode)
    return traj.to_dict()


MULTI = [
    {"calls": [call("get_account", customer_id="C101"),
               call("get_transactions", account_id="A101"),
               call("get_transactions", account_id="A999")]},
    {"final": "done"},
]


class TestSequentialVersusBatch(unittest.TestCase):
    def test_sequential_executes_first_call_and_discards_the_rest(self):
        p = analyze_trial(run(MULTI, "sequential"), exec_mode="sequential")
        self.assertEqual(p.model_response_iterations.value, 2)
        self.assertEqual((p.calls_proposed.value, p.calls_executed.value, p.calls_discarded.value), (3, 1, 2))
        self.assertEqual(p.discard_reasons, {DISCARD_SEQUENTIAL_EXTRA: 2})
        self.assertEqual([e.executed for e in p.events], [True, False, False])

    def test_batch_executes_every_call(self):
        p = analyze_trial(run(MULTI, "batch"), exec_mode="batch")
        self.assertEqual((p.calls_proposed.value, p.calls_executed.value, p.calls_discarded.value), (3, 3, 0))
        # executed calls can exceed iterations in batch mode: the two units differ
        self.assertGreater(p.calls_executed.value, p.model_response_iterations.value - 1)

    def test_same_model_output_gives_different_executed_counts_by_mode(self):
        seq = analyze_trial(run(MULTI, "sequential"), exec_mode="sequential")
        bat = analyze_trial(run(MULTI, "batch"), exec_mode="batch")
        self.assertEqual(seq.calls_proposed.value, bat.calls_proposed.value)
        self.assertNotEqual(seq.calls_executed.value, bat.calls_executed.value)

    def test_batch_stops_after_a_dispatch_exception(self):
        rec = run([{"calls": [call("get_account", customer_id="C101"),
                              call("no_such_tool"),
                              call("get_transactions", account_id="A101")]}], "batch")
        self.assertEqual(rec["termination_reason"], "tool_error")
        p = analyze_trial(rec, exec_mode="batch")
        self.assertEqual((p.calls_proposed.value, p.calls_executed.value, p.calls_discarded.value), (3, 2, 1))
        self.assertEqual(p.calls_failed.value, 1)
        self.assertEqual(p.discard_reasons, {DISCARD_AFTER_TERMINAL_ERROR: 1})

    def test_without_exec_mode_discards_are_unclassified(self):
        p = analyze_trial(run(MULTI, "sequential"))
        self.assertEqual(p.exec_mode.status, NOT_OBSERVABLE)
        self.assertEqual(p.discard_reasons, {DISCARD_UNCLASSIFIED: 2})
        self.assertEqual(p.calls_discarded.value, 2)

    def test_unknown_exec_mode_rejected(self):
        with self.assertRaises(ValueError):
            analyze_trial(run(MULTI), exec_mode="parallel")


class TestEmittedIsNotAssumed(unittest.TestCase):
    def test_emitted_is_indeterminate_unless_capture_is_declared_complete(self):
        rec = run(MULTI, "sequential")
        default = analyze_trial(rec, exec_mode="sequential")
        self.assertEqual(default.calls_emitted.status, INDETERMINATE)
        self.assertEqual(default.calls_emitted.value, 3)          # lower bound
        self.assertIn("lower bound", default.calls_emitted.reason)
        self.assertEqual(default.accounting_identity.status, INDETERMINATE)
        self.assertIsNone(default.accounting_identity.value["holds"])

        complete = analyze_trial(rec, exec_mode="sequential", adapter_capture_complete=True)
        self.assertEqual(complete.calls_emitted, Observed(3, VERIFIED))
        self.assertEqual(complete.accounting_identity, Observed({"holds": True, "residual": 0}, VERIFIED))
        self.assertEqual(complete.calls_emitted.value,
                         complete.calls_executed.value + complete.calls_discarded.value)

        incomplete = analyze_trial(rec, exec_mode="sequential", adapter_capture_complete=False)
        self.assertEqual(incomplete.calls_emitted.status, INDETERMINATE)
        self.assertEqual(incomplete.accounting_identity.status, INDETERMINATE)

    def test_a_proposed_call_is_not_counted_as_executed(self):
        # The model "emits" a call in its final-turn text only; nothing executed.
        rec = run([{"final": 'I would call {"tool": "get_account"}'}])
        p = analyze_trial(rec, exec_mode="sequential", adapter_capture_complete=True)
        self.assertEqual((p.calls_proposed.value, p.calls_executed.value), (0, 0))

    def test_unreconciled_record_is_flagged_not_blamed_on_agent(self):
        rec = run(MULTI, "batch")
        rec["steps"] = rec["steps"][:-1] + [dict(rec["steps"][-1], action="get_customer")]
        p = analyze_trial(rec, exec_mode="batch", adapter_capture_complete=True)
        self.assertEqual(p.accounting_identity.status, INDETERMINATE)
        self.assertFalse(p.accounting_identity.value["holds"])
        self.assertGreater(p.accounting_identity.value["residual"], 0)
        self.assertIn("never attributed to the agent", p.accounting_identity.reason)


class TestRetriesAndFailures(unittest.TestCase):
    def test_retries_are_not_observable_and_never_zero_by_assumption(self):
        p = analyze_trial(run(MULTI))
        self.assertEqual(p.retries.status, NOT_OBSERVABLE)
        self.assertIsNone(p.retries.value)

    def test_retries_verified_only_when_the_record_carries_them(self):
        rec = run(MULTI)
        rec["adapter_retries"] = 2
        self.assertEqual(analyze_trial(rec).retries, Observed(2, VERIFIED))
        rec["adapter_retries"] = True     # not an int count
        self.assertEqual(analyze_trial(rec).retries.status, NOT_OBSERVABLE)

    def test_failed_lookup_versus_empty_result(self):
        rec = run([call("get_customer", customer_id="C999"),          # error dict, non-terminal
                   call("get_transactions", account_id="A999"),       # empty list, not an error
                   call("get_account", customer_id="C101"),
                   {"final": "x"}])
        p = analyze_trial(rec, exec_mode="sequential")
        self.assertEqual(p.lookup_calls_executed.value, 3)
        self.assertEqual(p.lookup_calls_failed.value, 1)
        self.assertEqual(p.lookup_empty_results.value, 1)
        self.assertEqual(p.calls_failed.value, 1)

    def test_model_error_is_a_failed_request_not_a_call(self):
        rec = run([call("get_account", customer_id="C101"), RuntimeError("boom")])
        self.assertEqual(rec["termination_reason"], "model_error")
        p = analyze_trial(rec, exec_mode="sequential")
        self.assertEqual(p.model_requests_failed.value, 1)
        self.assertEqual(p.model_response_iterations.value, 1)
        self.assertEqual(p.calls_executed.value, 1)

    def test_invalid_action_proposes_nothing(self):
        rec = run([{"nonsense": 1}])
        self.assertEqual(rec["termination_reason"], "invalid_action")
        p = analyze_trial(rec, exec_mode="sequential")
        self.assertEqual((p.calls_proposed.value, p.calls_executed.value), (0, 0))
        self.assertEqual(p.accounting_identity.value["residual"], 0)


class TestIdentifiers(unittest.TestCase):
    def test_repeats_do_not_inflate_distinct_counts(self):
        rec = run([call("get_account", customer_id="C101"),
                   call("get_account", customer_id="C101"),
                   call("get_transactions", account_id="A101"),
                   {"final": "x"}])
        p = analyze_trial(rec, exec_mode="sequential")
        self.assertEqual(p.lookup_calls_executed.value, 3)
        self.assertEqual(p.distinct_identifiers_attempted.value, 2)     # C101, A101
        self.assertEqual(p.identifier_use_events.value, 3)
        self.assertEqual(p.identifier_use_events_executed.value, 3)
        self.assertGreater(p.identifier_emission_events.value, p.distinct_identifiers_observed.value)

    def test_identifier_proposed_only_in_a_discarded_call(self):
        p = analyze_trial(run(MULTI, "sequential"), exec_mode="sequential")
        # A101 and A999 were proposed but their calls were never dispatched.
        self.assertEqual(p.distinct_identifiers_attempted.value, 1)
        self.assertEqual(p.distinct_identifiers_proposed_only_in_discarded_calls.value, 2)
        self.assertEqual(p.identifier_use_events.value, 3)
        self.assertEqual(p.identifier_use_events_executed.value, 1)

    def test_calculate_is_not_a_lookup(self):
        rec = run([call("calculate", op="sum", values=[1, 2]), {"final": "3"}])
        p = analyze_trial(rec, exec_mode="sequential")
        self.assertEqual((p.calls_executed.value, p.lookup_calls_executed.value), (1, 0))


class TestForeignOrMissingHistory(unittest.TestCase):
    def test_langchain_style_history_is_not_reconstructed(self):
        rec = run(MULTI, "batch")
        rec["raw_history"] = [{"type": "HumanMessage", "content": "x"}, {"type": "AIMessage", "content": "y"}]
        p = analyze_trial(rec)
        self.assertEqual(p.history_format, "foreign")
        for f in (p.calls_proposed, p.calls_emitted, p.calls_discarded, p.accounting_identity,
                  p.model_response_iterations):
            self.assertEqual(f.status, NOT_OBSERVABLE)
            self.assertTrue(f.reason)
        self.assertEqual(p.calls_executed, Observed(3, VERIFIED))   # steps are still evidence

    def test_missing_history(self):
        rec = run(MULTI)
        del rec["raw_history"]
        self.assertEqual(analyze_trial(rec).history_format, "absent")


class TestContract(unittest.TestCase):
    def test_analysis_never_mutates_the_record(self):
        rec = run(MULTI, "batch")
        before = copy.deepcopy(rec)
        analyze_trial(rec, exec_mode="batch", adapter_capture_complete=True)
        analyze_trial(rec)
        self.assertEqual(rec, before)

    def test_ids_are_carried_through(self):
        p = analyze_trial(run(MULTI))
        self.assertEqual((p.run_id, p.task_id, p.experiment_id), ("run-1", "synthetic-provenance", "exp-synth"))

    def test_non_verified_value_must_carry_a_reason(self):
        with self.assertRaises(ValueError):
            Observed(None, NOT_OBSERVABLE)
        with self.assertRaises(ValueError):
            Observed(1, "probably")

    def test_deterministic_and_serializable(self):
        import json
        rec = run(MULTI, "batch")
        a = analyze_trial(rec, exec_mode="batch").to_dict()
        b = analyze_trial(rec, exec_mode="batch").to_dict()
        self.assertEqual(a, b)
        json.dumps(a)
        self.assertNotIn("events", a)

    def test_completeness_summary(self):
        c = analyze_trial(run(MULTI), exec_mode="sequential").completeness()
        self.assertEqual(c[NOT_OBSERVABLE], 1)          # retries
        self.assertEqual(c[INDETERMINATE], 2)           # emitted, identity


class TestAdapterCapture(unittest.TestCase):
    def test_gemini_style_deferred_calls_are_proposed_and_discarded_not_lost(self):
        rec = run([{"tool": "get_account", "arguments": {"customer_id": "C101"},
                    "deferred_tool_calls": [{"tool": "get_transactions", "arguments": {"account_id": "A101"}},
                                            {"tool": "search_policy", "arguments": {"query": "waiver"}}]},
                   {"final": "x"}])
        p = analyze_trial(rec, exec_mode="sequential")
        self.assertEqual((p.calls_proposed.value, p.calls_executed.value, p.calls_discarded.value), (3, 1, 2))
        self.assertEqual(p.discard_reasons, {DISCARD_ADAPTER_DEFERRED: 2})
        self.assertEqual(p.distinct_identifiers_proposed_only_in_discarded_calls.value, 2)
        self.assertEqual(p.accounting_identity.value["residual"], 0)

    def test_capture_completeness_is_only_asserted_where_it_is_certain(self):
        self.assertIs(capture_completeness_for("local-stub"), True)
        for dropping in ("openai", "groq", "openrouter", "omniroute"):
            self.assertIs(capture_completeness_for(dropping), False, dropping)
        for unverified in ("local", "gemini", "langchain", "something-new"):
            self.assertIsNone(capture_completeness_for(unverified), unverified)

    def test_first_call_only_adapters_never_yield_a_verified_emitted_count(self):
        rec = run(MULTI, "sequential")
        p = analyze_trial(rec, exec_mode="sequential", adapter_capture_complete=capture_completeness_for("openai"))
        self.assertEqual(p.calls_emitted.status, INDETERMINATE)
        self.assertIn("known to be incomplete", p.calls_emitted.reason)
        self.assertEqual(p.accounting_identity.status, INDETERMINATE)

    def test_no_real_adapter_has_observable_retries(self):
        for name, cap in ADAPTER_CAPABILITIES.items():
            if name != "local-stub":
                self.assertEqual(cap["retries"], "not_observable", name)
        self.assertEqual(analyze_trial(run(MULTI)).retries.status, NOT_OBSERVABLE)


class TestBudgetPlugin(unittest.TestCase):
    def test_default_is_no_policy(self):
        a = NoBudgetPolicy().assess(analyze_trial(run(MULTI)))
        self.assertEqual(a.status, "no_policy_configured")

    def test_iteration_cap_counts_iterations_not_tool_calls(self):
        p = analyze_trial(run(MULTI, "batch"), exec_mode="batch")   # 2 iterations, 3 executed calls
        self.assertEqual(IterationCapPolicy(2).assess(p).status, "within")
        self.assertEqual(IterationCapPolicy(1).assess(p).status, "exceeded")

    def test_cap_is_indeterminate_when_iterations_are_not_observable(self):
        rec = run(MULTI)
        del rec["raw_history"]
        self.assertEqual(IterationCapPolicy(5).assess(analyze_trial(rec)).status, "indeterminate")


if __name__ == "__main__":
    unittest.main()
