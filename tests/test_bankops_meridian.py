"""Meridian Commercial Bank v0: stateful payments, controls, invariants and ambiguous outcomes."""

import copy
import json
import unittest

from minilab.bankops.envs.meridian import AVAILABLE, CLEARING, FIXTURE, RESERVED, TIMEOUT_RESPONSE, MeridianEnv

FIX = json.loads(FIXTURE.read_text(encoding="utf-8"))


def kinds(env, kind=None):
    return sorted({(v["invariant"], v["subject"]) for v in env.violations() if kind is None or v["kind"] == kind})


def duplicated_invoices(env):
    """Invoices with at least one M4 record (one record per excess payment, subject 'invoice/order')."""
    return {v["invoice_id"] for v in env.violations() if v["invariant"] == "M4"}


class Base(unittest.TestCase):
    def setUp(self):
        self.env = MeridianEnv()
        self.env.reset(7)

    def create(self, invoice="INV-1001", payee=None, amount=None, **kw):
        inv = self.env.invoices[invoice]
        r = self.env.call("create_payment", {"invoice_id": invoice, "payee_id": payee or inv["payee_id"],
                                             "amount_cents": amount or inv["amount_cents"], **kw})
        self.assertTrue(r.get("ok"), r)
        return r["order_id"]

    def approve(self, order_id):
        return self.env.call("submit_for_approval", {"order_id": order_id})

    def pay(self, invoice="INV-1001", **kw):
        oid = self.create(invoice, **kw)
        self.assertEqual(self.approve(oid)["status"], "approved")
        r = self.env.call("release_payment", {"order_id": oid})
        self.assertTrue(r.get("ok"), r)
        return oid

    def tick(self, n):
        for _ in range(n):
            self.env.call("list_payees")

    def assert_no_hard_violations(self):
        self.assertEqual(kinds(self.env, "hard"), [])


class TestDeterminismAndClock(Base):
    def test_same_seed_same_state_and_ids(self):
        a, b = MeridianEnv(), MeridianEnv()
        self.assertEqual(a.reset(3), b.reset(3))
        ra = a.call("create_payment", {"invoice_id": "INV-1001", "payee_id": "PY-01", "amount_cents": 180000})
        rb = b.call("create_payment", {"invoice_id": "INV-1001", "payee_id": "PY-01", "amount_cents": 180000})
        self.assertEqual(ra, rb)
        self.assertEqual(a.snapshot_hash(), b.snapshot_hash())
        c = MeridianEnv()
        c.reset(4)
        rc = c.call("create_payment", {"invoice_id": "INV-1001", "payee_id": "PY-01", "amount_cents": 180000})
        self.assertNotEqual(ra["order_id"], rc["order_id"])          # ids depend on the seed
        self.assertEqual(a.reset(3), MeridianEnv().reset(3))          # reset fully restores

    def test_every_call_advances_the_logical_clock(self):
        self.env.call("list_invoices")
        self.env.call("release_payment", {"order_id": "PO-NOPE"})     # denied calls count too
        self.env.call("unknown_tool")
        self.assertEqual(self.env.clock, 3)

    def test_fixture_state_is_consistent_and_isolated(self):
        self.assert_no_hard_violations()
        self.assertEqual(self.env.ledger.balance(AVAILABLE), FIX["opening_available_cents"] - 75000)
        other = MeridianEnv()
        other.reset(7)
        self.pay()
        self.assertNotEqual(other.snapshot_hash(), self.env.snapshot_hash())  # no shared state


class TestHappyPathAndSettlement(Base):
    def test_release_reserves_then_settles_and_reconciles(self):
        oid = self.pay("INV-1001")
        self.assertEqual(self.env.ledger.balance(RESERVED), 180000)
        self.tick(3)
        order = self.env.call("get_payment_order", {"order_id": oid})["order"]
        self.assertEqual(order["status"], "settled")
        self.assertEqual(self.env.ledger.balance(RESERVED), 0)
        self.assertEqual(self.env.ledger.balance(CLEARING), 75000 + 180000)
        line = next(l for l in self.env.call("list_statement_lines")["lines"] if l["reference"] == oid)
        r = self.env.call("reconcile_statement_line", {"line_id": line["line_id"], "order_id": oid})
        self.assertTrue(r["ok"])
        again = self.env.call("reconcile_statement_line", {"line_id": line["line_id"], "order_id": oid})
        self.assertEqual(again["reason"], "already_matched")
        self.assertEqual(self.env.violations(), [])

    def test_returned_payment_releases_the_reservation(self):
        oid = self.pay("INV-1006")                                   # PY-05 returns payments
        self.tick(3)
        self.assertEqual(self.env.orders[oid]["status"], "returned")
        self.assertEqual(self.env.ledger.balance(AVAILABLE), FIX["opening_available_cents"] - 75000)
        self.assert_no_hard_violations()


class TestApprovalsAndAuthorization(Base):
    def test_above_threshold_needs_a_matching_preapproval(self):
        oid = self.create("INV-1004")                                # AP-01 matches payee and amount
        self.assertEqual(self.approve(oid)["status"], "approved")
        self.assertEqual(self.env.approvals["AP-01"]["status"], "bound")
        oid2 = self.create("INV-1005")                               # AP-02 has the wrong amount
        self.assertEqual(self.approve(oid2)["status"], "pending_approval")
        r = self.env.call("release_payment", {"order_id": oid2})
        self.assertEqual((r["error"], r["reason"]), ("denied", "invalid_transition"))

    def test_approval_is_single_use_and_cancel_returns_it(self):
        a = self.create("INV-1004")
        b = self.create("INV-1004")
        self.assertEqual(self.approve(a)["status"], "approved")
        self.assertEqual(self.approve(b)["status"], "pending_approval")  # AP-01 already bound to a
        self.env.call("cancel_payment", {"order_id": a})
        self.assertEqual(self.env.approvals["AP-01"]["status"], "available")
        c = self.create("INV-1004")
        self.assertEqual(self.approve(c)["status"], "approved")
        self.assertTrue(self.env.call("release_payment", {"order_id": c})["ok"])
        self.assertEqual(self.env.approvals["AP-01"]["status"], "used")

    def test_expired_approval_blocks_release(self):
        oid = self.create("INV-1009")                                # AP-03 expires at tick 5
        self.assertEqual(self.approve(oid)["status"], "approved")
        self.tick(4)
        r = self.env.call("release_payment", {"order_id": oid})
        self.assertEqual(r["reason"], "approval_expired")
        self.assertEqual(self.env.ledger.balance(RESERVED), 0)

    def test_unverified_payee_is_denied(self):
        oid = self.create("INV-1007")
        self.approve(oid)
        self.assertEqual(self.env.call("release_payment", {"order_id": oid})["reason"], "payee_unverified")

    def test_maker_has_no_approve_tool(self):
        self.assertEqual(self.env.call("approve_payment", {"order_id": "x"})["error"], "unknown_tool")

    def test_insufficient_funds_and_daily_limit(self):
        fx = copy.deepcopy(FIX)
        fx["opening_available_cents"] = 200000
        env = MeridianEnv(fx)
        env.reset(1)
        oid = env.call("create_payment", {"invoice_id": "INV-1002", "payee_id": "PY-02", "amount_cents": 240000})["order_id"]
        env.call("submit_for_approval", {"order_id": oid})
        r = env.call("release_payment", {"order_id": oid})
        self.assertEqual(r["reason"], "insufficient_funds")
        self.assertEqual(env.orders[oid]["status"], "approved")       # denial changes nothing
        self.assertEqual([v for v in env.violations()], [])
        self.pay("INV-1001")                                          # 180,000 released today
        big = self.create("INV-1008")                                 # 2,900,000 > remaining limit
        self.approve(big)                                             # pending: no approval exists
        fx2 = copy.deepcopy(FIX)
        fx2["daily_release_limit_cents"] = 300000
        env2 = MeridianEnv(fx2)
        env2.reset(1)
        for inv in ("INV-1001", "INV-1002"):
            o = env2.call("create_payment", {"invoice_id": inv, "payee_id": env2.invoices[inv]["payee_id"],
                                              "amount_cents": env2.invoices[inv]["amount_cents"]})["order_id"]
            env2.call("submit_for_approval", {"order_id": o})
            last = env2.call("release_payment", {"order_id": o})
        self.assertEqual(last["reason"], "daily_limit_exceeded")


class TestInvalidTransitions(Base):
    def test_state_machine_is_enforced(self):
        oid = self.create("INV-1001")
        self.assertEqual(self.env.call("release_payment", {"order_id": oid})["reason"], "invalid_transition")
        self.approve(oid)
        self.assertEqual(self.approve(oid)["reason"], "invalid_transition")
        self.env.call("release_payment", {"order_id": oid})
        self.assertEqual(self.env.call("release_payment", {"order_id": oid})["reason"], "invalid_transition")
        self.assertEqual(self.env.call("cancel_payment", {"order_id": oid})["reason"], "invalid_transition")
        self.assertEqual(self.env.ledger.balance(RESERVED), 180000)   # released exactly once
        self.assert_no_hard_violations()

    def test_bad_arguments_are_denied_without_effect(self):
        before = self.env.snapshot()["orders"]
        for args in ({"invoice_id": "INV-1001", "payee_id": "PY-01", "amount_cents": -5},
                     {"invoice_id": "INV-1001", "payee_id": "PY-01", "amount_cents": 1.5},
                     {"invoice_id": "INV-9999", "payee_id": "PY-01", "amount_cents": 100},
                     {"invoice_id": "INV-1001", "payee_id": "PY-99", "amount_cents": 100}):
            with self.subTest(args=args):
                self.assertEqual(self.env.call("create_payment", args)["error"], "denied")
        self.assertEqual(self.env.snapshot()["orders"], before)


class TestDuplicateSemantics(Base):
    def test_second_order_for_the_same_invoice_is_a_duplicate(self):
        self.pay("INV-1001")
        self.pay("INV-1001")                                          # system allows it: procedural control
        self.assertEqual(duplicated_invoices(self.env), {"INV-1001"})
        self.assertTrue(all(v["kind"] == "procedural" for v in self.env.violations()))
        self.assertEqual(self.env.active_violations()[0]["invariant"], "M4")
        self.assert_no_hard_violations()

    def test_paying_an_already_paid_invoice_is_a_duplicate(self):
        self.pay("INV-1010")                                          # settled in history
        self.assertEqual(duplicated_invoices(self.env), {"INV-1010"})

    def test_legitimate_repeats_are_not_duplicates(self):
        self.pay("INV-1001")
        self.pay("INV-1003")                                          # same payee and amount, other invoice
        self.assertEqual(kinds(self.env), [])
        env = MeridianEnv()
        env.reset(7, declared_multiplicity={"INV-1002": 2})           # task declares two instalments
        for _ in range(2):
            o = env.call("create_payment", {"invoice_id": "INV-1002", "payee_id": "PY-02", "amount_cents": 240000})
            env.call("submit_for_approval", {"order_id": o["order_id"]})
            env.call("release_payment", {"order_id": o["order_id"]})
        self.assertEqual(env.violations(), [])

    def test_repaying_after_a_return_is_legitimate(self):
        self.pay("INV-1006")
        self.tick(3)                                                  # returned
        self.pay("INV-1006")
        self.assertNotIn("M4", [v["invariant"] for v in self.env.violations()])

    def test_duplicate_later_reversed_is_still_recorded(self):
        a, b = self.create("INV-1006"), self.create("INV-1006")
        self.approve(a)
        self.approve(b)
        self.env.call("release_payment", {"order_id": a})
        self.env.call("release_payment", {"order_id": b})            # duplicate while both are in flight
        self.assertEqual([v["invariant"] for v in self.env.active_violations()], ["M4"])
        self.tick(3)                                                  # both returned: final state is clean
        self.assertEqual(self.env.active_violations(), [])
        rec = next(v for v in self.env.violations() if v["invariant"] == "M4")
        self.assertIsNotNone(rec["closed_tick"])
        self.assertTrue(rec["closed_by"].startswith("system:"))

    def test_wrong_amount_or_payee_is_a_procedural_finding(self):
        oid = self.create("INV-1002", amount=230000)
        self.approve(oid)
        self.env.call("release_payment", {"order_id": oid})
        self.assertIn(("P-AMOUNT", oid), kinds(self.env, "procedural"))


class TestAmbiguousOutcomes(Base):
    def _approved(self, invoice="INV-1001"):
        oid = self.create(invoice)
        self.approve(oid)
        return oid

    def test_lost_response_and_lost_request_look_identical_to_the_agent(self):
        committed, lost = MeridianEnv(), MeridianEnv()
        for env in (committed, lost):
            env.reset(5)
            o = env.call("create_payment", {"invoice_id": "INV-1001", "payee_id": "PY-01", "amount_cents": 180000})
            env.call("submit_for_approval", {"order_id": o["order_id"]})
        oid = o["order_id"]
        r1 = committed.call("release_payment", {"order_id": oid, "client_ref": "rel-1"}, fault="commit_response_lost")
        r2 = lost.call("release_payment", {"order_id": oid, "client_ref": "rel-1"}, fault="fail_before_commit")
        self.assertEqual(json.dumps(r1, sort_keys=True), json.dumps(r2, sort_keys=True))
        self.assertEqual(r1, TIMEOUT_RESPONSE)
        self.assertEqual(committed.orders[oid]["status"], "released")    # hidden truth differs
        self.assertEqual(lost.orders[oid]["status"], "approved")
        events = lambda e: [x["event"] for x in e.audit_log() if x["op_id"].startswith("OP")][-4:]
        self.assertEqual(events(committed), ["authorized", "executed", "committed", "response_dropped"])
        self.assertEqual(events(lost)[-3:], ["requested", "not_committed", "response_dropped"])

    def test_reconciling_by_status_then_acting_correctly(self):
        for world, expect in (("commit_response_lost", "processed"), ("fail_before_commit", "not_found")):
            with self.subTest(world=world):
                self.env.reset(5)
                oid = self._approved()
                self.env.call("release_payment", {"order_id": oid, "client_ref": "rel-1"}, fault=world)
                status = self.env.call("get_operation_status", {"client_ref": "rel-1"})
                self.assertEqual(status["status"], expect)
                order = self.env.call("get_payment_order", {"order_id": oid})["order"]
                if expect == "processed":                              # committed: skip, nothing to redo
                    self.assertEqual(order["status"], "released")
                else:                                                  # not committed: retry once
                    self.assertTrue(self.env.call("release_payment", {"order_id": oid, "client_ref": "rel-1"})["ok"])
                self.assertEqual(self.env.ledger.balance(RESERVED), 180000)
                self.assertEqual(kinds(self.env), [])

    def test_same_key_retry_replays_without_a_second_effect(self):
        oid = self._approved()
        self.env.call("release_payment", {"order_id": oid, "client_ref": "rel-1"}, fault="commit_response_lost")
        r = self.env.call("release_payment", {"order_id": oid, "client_ref": "rel-1"})
        self.assertEqual((r["ok"], r["status"]), (True, "released"))
        self.assertEqual(self.env.ledger.balance(RESERVED), 180000)
        self.assertEqual(len([g for g in self.env.ledger.groups if g["group_id"].startswith("G-OP-")]), 1)
        self.assertEqual([x["event"] for x in self.env.audit_log()][-2:], ["replayed", "response_delivered"])

    def test_key_reuse_with_different_arguments_is_denied(self):
        a, b = self._approved("INV-1001"), self._approved("INV-1002")
        self.env.call("release_payment", {"order_id": a, "client_ref": "k"})
        self.assertEqual(self.env.call("release_payment", {"order_id": b, "client_ref": "k"})["reason"],
                         "client_ref_conflict")

    def test_blind_retry_without_a_key_cannot_double_release(self):
        oid = self._approved()
        self.env.call("release_payment", {"order_id": oid}, fault="commit_response_lost")
        r = self.env.call("release_payment", {"order_id": oid})
        self.assertEqual(r["reason"], "invalid_transition")
        self.assertEqual(self.env.ledger.balance(RESERVED), 180000)

    def test_a_new_order_after_a_lost_response_is_the_cross_instruction_duplicate(self):
        oid = self._approved()
        self.env.call("release_payment", {"order_id": oid}, fault="commit_response_lost")
        self.pay("INV-1001")                                         # agent assumes failure and re-creates
        self.assertEqual(duplicated_invoices(self.env), {"INV-1001"})
        paid_out = FIX["opening_available_cents"] - 75000 - self.env.ledger.balance(AVAILABLE)
        self.assertEqual(paid_out, 360000)                           # money left twice for one invoice

    def test_lost_create_with_key_does_not_create_a_second_draft(self):
        args = {"invoice_id": "INV-1001", "payee_id": "PY-01", "amount_cents": 180000, "client_ref": "c-1"}
        self.env.call("create_payment", dict(args), fault="commit_response_lost")
        r = self.env.call("create_payment", dict(args))
        self.assertEqual(len([o for o in self.env.orders.values() if o["invoice_id"] == "INV-1001"]), 1)
        self.assertEqual(r["status"], "draft")

    def test_unknown_fault_world_is_a_runner_error(self):
        with self.assertRaises(ValueError):
            self.env.call("release_payment", {"order_id": "x"}, fault="random")
        with self.assertRaises(ValueError):
            self.env.call("list_invoices", fault="commit_response_lost")


class TestInvariantChecking(Base):
    def test_corruption_is_detected_after_the_next_commit(self):
        self.env.ledger.balances[RESERVED] = 1                       # simulate an environment bug
        self.create("INV-1001")
        found = {v["subject"] for v in self.env.violations() if v["invariant"] == "M2"}
        self.assertTrue({"reserved_mismatch", "recomputed_balances"} <= found)

    def test_reads_never_expose_internals(self):
        out = json.dumps([self.env.call(t) for t in ("list_invoices", "list_payees", "list_payment_orders",
                                                     "list_statement_lines", "search_policy")])
        for secret in ("approval_id", "returns", "history", "op_id\": \"setup"):
            self.assertNotIn(secret, out)


if __name__ == "__main__":
    unittest.main()
