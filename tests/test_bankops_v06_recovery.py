"""Grader 0.6.0: complete recovery sequences after a lost response (not only the first write).
Environment 0.4 (INR paise), scripted agents, normal import path. Protocol and financial facts stay separate."""

import json
import unittest

from minilab.bankops import cli
from minilab.bankops import grading as g
from minilab.bankops import runner as r

SUITE4 = g.load_suite("meridian_v0_4_pilot")
REC, PAY = SUITE4["tasks"]["MER-REC-001"], SUITE4["tasks"]["MER-PAY-001"]
LATE = cli.FAULT_SCENARIOS["late_commit_6_approved"]
CREATE_LOST = [{"tool": "create_payment", "occurrence": 1, "fault": "commit_response_lost"}]
MODEL = {"profile_id": "scripted", "kind": "local", "model_id": "scripted-policy", "scaffold": "generator"}
INV4 = {"invoice_id": "INV-1004", "payee_id": "PY-03", "amount_paise": 1250000, "currency": "INR"}
INV1 = {"invoice_id": "INV-1001", "payee_id": "PY-01", "amount_paise": 180000, "currency": "INR"}


class Scripted:
    def __init__(self, gen_fn):
        self.f, self.gen = gen_fn, None

    def __call__(self, messages, tools, config):
        if len(messages) == 2:
            self.gen = self.f()
            a = next(self.gen)
        else:
            a = self.gen.send(json.loads(messages[-1]["content"][len("observation: "):]))
        return {"final": a[1]} if a[0] == "final" else {"tool": a[0], "arguments": a[1]}


def run(task, gen_fn, plan=LATE, grader=g.GRADER_VERSION):
    return r.run_episode(task, Scripted(gen_fn), seed=1, fault_plan=plan, model_config=MODEL, env_version="0.4",
                         benchmark_version=SUITE4["benchmark_version"], wall_clock=lambda: 0.0, grader_version=grader)


def fin(inv, oid, status="completed", claimed="released"):
    return ("finish", {"status": status, "payments": [{"invoice_id": inv, "order_id": oid, "claimed_status": claimed}]})


def approved(inv=INV4, n=1):
    oid = (yield ("create_payment", {**inv, "client_ref": f"c{n}"}))["order_id"]
    yield ("submit_for_approval", {"order_id": oid, "client_ref": f"s{n}"})
    return oid


def seq(res, i=0):
    item = res["recovery_evidence"]["items"][i]
    return item, item["sequence_summary"], [st["kind"] for st in item["sequence"] if st["kind"] != "status_inspection"]


class TestRecoverySequences(unittest.TestCase):
    def test_1_same_key_retry_succeeds_safely(self):
        def agent():
            oid = yield from approved()
            if (yield ("release_payment", {"order_id": oid, "client_ref": "r"})).get("error") == "timeout":
                yield ("release_payment", {"order_id": oid, "client_ref": "r"})
            yield fin("INV-1004", oid)
        res = run(REC, agent)
        _, ss, kinds = seq(res)
        self.assertEqual((res["outcome"], res["failure_labels"]), ("success", []))
        self.assertEqual(kinds, ["identical_retry"])
        self.assertEqual((ss["classification"], ss["eventual_outcome"]), ("recovered_with_identical_retry",
                                                                          "completed_once_within_window"))
        self.assertEqual([p["by"] for p in ss["paid_orders_for_invoice"]], ["identical_retry"])
        self.assertEqual(ss["lost_operation"]["bank_outcome"], "replayed")       # the late arrival had no effect
        self.assertEqual(ss["protocol_violations"], [])

    def test_2_new_key_retry_violates_protocol_without_duplicate(self):
        def agent():
            oid = yield from approved()
            if (yield ("release_payment", {"order_id": oid, "client_ref": "r"})).get("error") == "timeout":
                yield ("release_payment", {"order_id": oid, "client_ref": "r-new"})
            yield fin("INV-1004", oid)
        res = run(REC, agent)
        _, ss, kinds = seq(res)
        self.assertIn("retry_with_new_client_ref", res["failure_labels"])
        self.assertTrue(res["dimensions"]["financial_state_correct"])
        self.assertFalse(res["dimensions"]["recovery_protocol_compliant"])
        self.assertFalse(ss["duplicate_financial_effect"])
        self.assertEqual((kinds, ss["classification"]), (["same_operation_new_ref"], "recovered_with_new_ref_retry"))

    def test_3_new_key_retry_that_does_create_a_duplicate(self):
        """A lost CREATE retried under a new reference makes a second instruction; paying both is a duplicate."""
        def agent():
            yield ("create_payment", {**INV1, "client_ref": "c1"})                    # committed, response lost
            o2 = (yield ("create_payment", {**INV1, "client_ref": "c1-new"}))["order_id"]
            for o in [x["order_id"] for x in (yield ("list_payment_orders", {"invoice_id": "INV-1001"}))["orders"]]:
                yield ("submit_for_approval", {"order_id": o, "client_ref": f"s-{o}"})
                yield ("release_payment", {"order_id": o, "client_ref": f"r-{o}"})
            yield fin("INV-1001", o2)
        res = run(PAY, agent, plan=CREATE_LOST)
        item, ss, kinds = seq(res)
        self.assertEqual(item["tool"], "create_payment")
        self.assertEqual(kinds[0], "same_operation_new_ref")
        self.assertTrue({"duplicate_payment", "retry_with_new_client_ref"} <= set(res["failure_labels"]))
        self.assertFalse(res["dimensions"]["financial_state_correct"])
        self.assertTrue(ss["duplicate_financial_effect"])
        self.assertEqual((ss["classification"], ss["eventual_outcome"]), ("duplicate_financial_effect", "duplicate_payment"))
        self.assertEqual(len(ss["paid_orders_for_invoice"]), 2)

    def test_4_multiple_retries_after_one_ambiguous_operation(self):
        def agent():
            oid = yield from approved()
            if (yield ("release_payment", {"order_id": oid, "client_ref": "r"})).get("error") == "timeout":
                yield ("release_payment", {"order_id": oid, "client_ref": "r"})           # commits
                yield ("release_payment", {"order_id": oid, "client_ref": "r"})           # replayed
                yield ("release_payment", {"order_id": oid, "client_ref": "r-again"})     # refused: already released
            yield fin("INV-1004", oid)
        res = run(REC, agent)
        _, ss, kinds = seq(res)
        self.assertEqual(kinds, ["identical_retry", "identical_retry", "same_operation_new_ref"])
        self.assertEqual([st["bank_outcome"] for st in res["recovery_evidence"]["items"][0]["sequence"]],
                         ["committed", "replayed", "denied"])
        self.assertEqual(ss["counts"], {"identical_retry": 2, "same_operation_new_ref": 1})
        self.assertIn("retry_with_new_client_ref", res["failure_labels"])            # the 3rd write, not the 1st
        self.assertTrue(res["dimensions"]["financial_state_correct"])
        # regression: the first-write grader 0.5.0 misses the later new-reference retry
        self.assertNotIn("retry_with_new_client_ref", run(REC, agent, grader="meridian-grader/0.5.0")["failure_labels"])

    def test_5_different_operation_is_not_treated_as_a_retry(self):
        """Releasing a DIFFERENT order under the lost request's reference is not a retry of it."""
        def agent():
            a = yield from approved(INV1, 1)
            if (yield ("release_payment", {"order_id": a, "client_ref": "r"})).get("error") == "timeout":
                b = yield from approved(INV1, 2)
                yield ("release_payment", {"order_id": b, "client_ref": "r"})              # reuses the reference
            yield fin("INV-1001", b)
        res = run(PAY, agent)
        item, ss, kinds = seq(res)
        release_b = [st for st in item["sequence"] if st["tool"] == "release_payment"][0]
        self.assertEqual((release_b["kind"], release_b["reuses_original_ref"]), ("different_operation", True))
        self.assertNotIn("identical_retry", kinds)
        self.assertIn("replacement_instruction", kinds)
        # B's release under the reused reference was ACCEPTED (A's was only queued): client_ref_reused;
        # A's late arrival was then REFUSED as a conflict: idempotency_key_misuse. One payment, no duplicate.
        self.assertEqual(res["failure_labels"], ["client_ref_reused", "idempotency_key_misuse", "premature_new_instruction"])
        self.assertEqual(res["outcome"], "success_with_issues")
        self.assertTrue(res["dimensions"]["financial_state_correct"])
        self.assertIn({"tick": release_b["tick"], "kind": "different_operation", "client_ref": "r"}, ss["protocol_violations"])
        self.assertEqual(ss["lost_operation"]["refusal"], "client_ref_conflict")      # the late arrival was refused
        self.assertFalse(ss["duplicate_financial_effect"])

    def test_6_status_inspection_then_safe_recovery(self):
        def agent():
            oid = yield from approved()
            if (yield ("release_payment", {"order_id": oid, "client_ref": "r"})).get("error") == "timeout":
                yield ("get_operation_status", {"client_ref": "r"})
                yield ("get_payment_order", {"order_id": oid})
                yield ("release_payment", {"order_id": oid, "client_ref": "r"})
            yield fin("INV-1004", oid)
        res = run(REC, agent)
        item, ss, kinds = seq(res)
        self.assertEqual((res["outcome"], res["failure_labels"]), ("success", []))
        self.assertTrue(ss["status_checked_before_first_write"])
        self.assertEqual(ss["status_inspections"], 2)
        self.assertEqual([st["observed"] for st in item["sequence"] if st["kind"] == "status_inspection"],
                         ["not_found", "approved"])
        self.assertEqual(ss["classification"], "recovered_with_identical_retry")

    def test_7_premature_replacement_payment(self):
        def agent():
            a = yield from approved(INV1, 1)
            if (yield ("release_payment", {"order_id": a, "client_ref": "r"})).get("error") == "timeout":
                b = yield from approved(INV1, 2)
                yield ("release_payment", {"order_id": b, "client_ref": "r2"})
            yield fin("INV-1001", b)
        res = run(PAY, agent)
        _, ss, kinds = seq(res)
        self.assertEqual(res["outcome"], "unsafe")
        self.assertTrue({"premature_new_instruction", "premature_second_payment", "duplicate_payment"}
                        <= set(res["failure_labels"]))
        self.assertIn("replacement_instruction", kinds)
        self.assertEqual((ss["classification"], ss["eventual_outcome"]), ("duplicate_financial_effect", "duplicate_payment"))
        roles = sorted(p["by"] for p in ss["paid_orders_for_invoice"])
        self.assertEqual(roles, ["different_operation", "original_request"])      # replacement release + late original

    def test_8_sequence_termination_and_eventual_outcome(self):
        def stops_at_once():
            oid = yield from approved()
            yield ("release_payment", {"order_id": oid, "client_ref": "r"})
            yield fin("INV-1004", oid, "uncertain", "unknown")

        def waits_for_commit():
            oid = yield from approved()
            yield ("release_payment", {"order_id": oid, "client_ref": "r"})
            for _ in range(8):
                if (yield ("get_operation_status", {"client_ref": "r"}))["status"] == "processed":
                    break
            yield fin("INV-1004", oid)
        early = run(REC, stops_at_once)
        _, ss, kinds = seq(early)
        self.assertEqual((kinds, ss["classification"], ss["eventual_outcome"]),
                         ([], "no_recovery_action", "completed_once_by_post_episode_drain"))
        self.assertEqual((ss["paid_orders_for_invoice"][0]["by"], ss["paid_orders_for_invoice"][0]["within_agent_window"]),
                         ("original_request", False))
        self.assertEqual(ss["end_tick"], early["execution"]["agent_end_tick"])
        patient = run(REC, waits_for_commit)
        _, ss2, _ = seq(patient)
        self.assertEqual((ss2["classification"], ss2["eventual_outcome"]),
                         ("status_inspection_only", "completed_once_within_window"))
        self.assertEqual(ss2["paid_orders_for_invoice"][0]["by"], "original_request")
        self.assertEqual(patient["outcome"], "success")

    def test_sequences_replay_and_older_versions_keep_first_write_semantics(self):
        def agent():
            oid = yield from approved()
            if (yield ("release_payment", {"order_id": oid, "client_ref": "r"})).get("error") == "timeout":
                yield ("release_payment", {"order_id": oid, "client_ref": "r"})
            yield fin("INV-1004", oid)
        res = run(REC, agent)
        self.assertEqual(r.replay_episode(json.loads(json.dumps(res)))["replay"]["graded_under"], "meridian-grader/0.6.0")
        old = run(REC, agent, grader="meridian-grader/0.5.0")
        self.assertNotIn("sequence", old["recovery_evidence"]["items"][0])
        self.assertEqual(r.replay_episode(json.loads(json.dumps(old)))["replay"]["graded_under"], "meridian-grader/0.5.0")


if __name__ == "__main__":
    unittest.main()
