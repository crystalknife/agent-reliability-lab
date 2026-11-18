"""Delayed commit (fault world `late_commit`) in Meridian v0: lifecycle, hidden-state equivalence,
retry, cancellation, competing payments, reconciliation, expiry, atomicity and replay."""

import copy
import json
import unittest

from minilab.bankops.envs import meridian as mer
from minilab.bankops.envs.meridian import AVAILABLE, RESERVED, TIMEOUT_RESPONSE, FIXTURE, MeridianEnv

BASE = json.loads(FIXTURE.read_text(encoding="utf-8"))
K = 4          # commit delay used throughout (accepted at tick t, commits at t + K)
DELTA = BASE["settlement_delay_ticks"]


def approved(env, invoice="INV-1001"):
    inv = env.invoices[invoice]
    oid = env.call("create_payment", {"invoice_id": invoice, "payee_id": inv["payee_id"],
                                      "amount_cents": inv["amount_cents"]})["order_id"]
    env.call("submit_for_approval", {"order_id": oid})
    return oid


def fresh(seed=5, fixture=None):
    env = MeridianEnv(fixture)
    env.reset(seed)
    return env


def late_release(env, oid, key=None, k=K):
    args = {"order_id": oid, **({"client_ref": key} if key else {})}
    return env.call("release_payment", args, fault="late_commit", commit_delay_ticks=k)


def idle(env, n):
    for _ in range(n):
        env.call("list_payees")


def op_events(env, op_id):
    return [(e["event"], e["tick"]) for e in env.audit_log() if e["op_id"] == op_id]


def paid_for(env, invoice="INV-1001"):
    return [o["order_id"] for o in env.orders.values() if o["invoice_id"] == invoice
            and o["status"] in ("released", "settled")]


class TestLifecycleAndAudit(unittest.TestCase):
    def test_authorized_queued_then_committed_then_settled(self):
        env = fresh()
        oid = approved(env)
        self.assertEqual(late_release(env, oid), TIMEOUT_RESPONSE)
        t = env.clock
        op = list(env.ops.ops.values())[-1]
        self.assertEqual((op.state, op.response_state), ("queued", "dropped"))
        self.assertEqual(env.orders[oid]["status"], "approved")          # nothing booked during the window
        self.assertEqual(env.ledger.balance(RESERVED), 0)
        self.assertEqual(env.approvals[env.orders[oid]["approval_id"]]["status"], "bound")
        idle(env, K)                                                       # tick t+K: core commits first
        self.assertEqual(env.orders[oid]["status"], "released")
        self.assertEqual(env.orders[oid]["released_tick"], t + K)
        idle(env, DELTA)
        self.assertEqual(env.orders[oid]["status"], "settled")
        self.assertEqual(op_events(env, op.op_id), [("requested", t), ("authorized", t), ("queued", t),
                                                    ("response_dropped", t), ("executed", t + K),
                                                    ("committed", t + K)])
        settle = [e for e in env.audit_log() if e["event"] == "settled" and e["order_id"] == oid]
        self.assertEqual(settle[0]["tick"], t + K + DELTA)                  # settlement is a separate event
        self.assertEqual(env.violations(), [])

    def test_runner_validation_rejects_bad_configurations_without_side_effects(self):
        env = fresh()
        oid = approved(env)
        before = (env.clock, env.snapshot_hash())
        bad = [("release_payment", {"order_id": oid}, {"fault": "late_commit"}),
               ("release_payment", {"order_id": oid}, {"fault": "late_commit", "commit_delay_ticks": 0}),
               ("release_payment", {"order_id": oid}, {"fault": "late_commit", "commit_delay_ticks": True}),
               ("create_payment", {"invoice_id": "INV-1002"}, {"fault": "late_commit", "commit_delay_ticks": 2}),
               ("release_payment", {"order_id": oid}, {"commit_delay_ticks": 2}),
               ("list_invoices", {}, {"fault": "late_commit", "commit_delay_ticks": 2})]
        for tool, args, kw in bad:
            with self.subTest(tool=tool, kw=kw), self.assertRaises(ValueError):
                env.call(tool, args, **kw)
        self.assertEqual((env.clock, env.snapshot_hash()), before)


class TestDistinguishability(unittest.TestCase):
    """During the window the delayed-commit world must look exactly like the request-lost world;
    after the commit it must look like the committed world. Committed-response-lost differs at once."""

    READS = [("get_operation_status", {"client_ref": "r"}), ("list_payment_orders", {}),
             ("list_statement_lines", {}), ("list_invoices", {}), ("get_invoice", {"invoice_id": "INV-1001"})]

    WINDOW = 12   # longer than the 8 calls observed below, so the whole observation is inside the window

    def _world(self, fault):
        env = fresh()
        oid = approved(env)
        other = approved(env, "INV-1002")
        kw = {"commit_delay_ticks": self.WINDOW} if fault == "late_commit" else {}
        first = env.call("release_payment", {"order_id": oid, "client_ref": "r"}, fault=fault, **kw)
        return env, oid, other, first

    def _observe(self, env, oid, other):
        out = [env.call(t, a) for t, a in self.READS]
        out.append(env.call("get_payment_order", {"order_id": oid}))
        out.append(env.call("cancel_payment", {"order_id": "PO-NONE"}))        # a denial carries no hints
        return out

    def test_window_is_indistinguishable_from_request_lost(self):
        (late, o1, x1, r1), (lost, o2, x2, r2) = self._world("late_commit"), self._world("fail_before_commit")
        self.assertEqual(r1, r2)
        self.assertEqual(o1, o2)
        self.assertEqual(self._observe(late, o1, x1), self._observe(lost, o2, x2))   # 7 reads, ticks t+1..t+3
        # a competing release in the window sees identical balances and limits in both worlds
        self.assertEqual(late.call("release_payment", {"order_id": x1}), lost.call("release_payment", {"order_id": x2}))

    def test_after_the_commit_it_matches_the_committed_world(self):
        late, o1, _, _ = self._world("late_commit")
        idle(late, self.WINDOW)
        status = late.call("get_operation_status", {"client_ref": "r"})
        self.assertEqual((status["status"], status["outcome"]), ("processed", "committed"))
        self.assertEqual(late.call("get_payment_order", {"order_id": o1})["order"]["status"], "released")

    def test_committed_response_lost_is_visible_immediately(self):
        crl, oid, _, _ = self._world("commit_response_lost")
        self.assertEqual(crl.call("get_operation_status", {"client_ref": "r"})["status"], "processed")
        late, _, _, _ = self._world("late_commit")
        self.assertEqual(late.call("get_operation_status", {"client_ref": "r"})["status"], "not_found")


class TestAgentActionsDuringTheWindow(unittest.TestCase):
    def test_same_key_retry_commits_once_and_suppresses_the_late_arrival(self):
        env = fresh()
        oid = approved(env)
        late_release(env, oid, key="r")
        late_op = list(env.ops.ops.values())[-1].op_id
        retry = env.call("release_payment", {"order_id": oid, "client_ref": "r"})
        self.assertTrue(retry["ok"])                                     # the core does not know the key yet
        idle(env, K)
        self.assertEqual(env.ops.ops[late_op].state, "replayed")
        self.assertEqual(env.ledger.balance(RESERVED) + env.ledger.balance("gl:clearing") - 75000, 180000)
        self.assertEqual(paid_for(env), [oid])
        self.assertEqual(env.violations(), [])

    def test_keyless_retry_of_the_same_order_is_protected_by_the_state_machine(self):
        env = fresh()
        oid = approved(env)
        late_release(env, oid)
        late_op = list(env.ops.ops.values())[-1].op_id
        self.assertTrue(env.call("release_payment", {"order_id": oid})["ok"])
        idle(env, K)
        op = env.ops.ops[late_op]
        self.assertEqual((op.state, op.detail["reason"], op.detail["at_commit"]), ("denied", "invalid_transition", True))
        self.assertEqual(paid_for(env), [oid])
        self.assertEqual(env.violations(), [])

    def test_cancel_during_the_window_wins_and_frees_the_approval(self):
        env = fresh(fixture=None)
        oid = env.call("create_payment", {"invoice_id": "INV-1004", "payee_id": "PY-03",
                                          "amount_cents": 1250000})["order_id"]
        env.call("submit_for_approval", {"order_id": oid})               # binds pre-approval AP-01
        late_release(env, oid)
        late_op = list(env.ops.ops.values())[-1].op_id
        self.assertTrue(env.call("cancel_payment", {"order_id": oid})["ok"])
        self.assertEqual(env.approvals["AP-01"]["status"], "available")
        idle(env, K)
        self.assertEqual(env.ops.ops[late_op].state, "denied")
        self.assertEqual(env.orders[oid]["status"], "cancelled")
        self.assertEqual(env.ledger.balance(RESERVED), 0)
        self.assertEqual(len([g for g in env.ledger.groups if g["op_id"] == late_op]), 0)
        status = env.call("get_operation_status", {"op_id": late_op})
        self.assertEqual((status["outcome"], status["reason"]), ("denied", "invalid_transition"))

    def test_competing_payment_drains_funds_so_the_late_commit_is_denied(self):
        fx = copy.deepcopy(BASE)
        fx["opening_available_cents"] = 75000 + 250000                   # after history: 250,000 available
        env = fresh(fixture=fx)
        a = approved(env, "INV-1001")                                     # 180,000
        b = approved(env, "INV-1002")                                     # 240,000
        late_release(env, a)
        late_op = list(env.ops.ops.values())[-1].op_id
        self.assertTrue(env.call("release_payment", {"order_id": b})["ok"])   # uses the same balance first
        idle(env, K)
        op = env.ops.ops[late_op]
        self.assertEqual((op.state, op.detail["reason"]), ("denied", "insufficient_funds"))
        self.assertEqual(env.orders[a]["status"], "approved")              # never paid
        self.assertGreaterEqual(env.ledger.balance(AVAILABLE), 0)
        self.assertEqual([v for v in env.violations() if v["kind"] == "hard"], [])

    def test_new_order_for_the_same_invoice_becomes_a_duplicate_when_the_late_commit_lands(self):
        env = fresh()
        oid = approved(env)
        late_release(env, oid, k=8)                                       # window covers the next 4 calls
        late_op = list(env.ops.ops.values())[-1].op_id
        self.assertEqual(env.call("get_operation_status", {"client_ref": "none"})["status"], "not_found")
        oid2 = approved(env)                                              # premature: "it failed, pay again"
        env.call("release_payment", {"order_id": oid2})
        self.assertEqual({v["invariant"] for v in env.violations()}, set())   # looks fine so far
        idle(env, 8)
        self.assertEqual(sorted(paid_for(env)), sorted([oid, oid2]))
        m4 = [v for v in env.violations() if v["invariant"] == "M4"]
        self.assertEqual(len(m4), 1)
        self.assertEqual(m4[0]["opened_by"], late_op)                    # the late commit created the breach


class TestApprovalRulesAcrossTheWindow(unittest.TestCase):
    def test_expiry_is_judged_at_acceptance(self):
        env = fresh()
        oid = env.call("create_payment", {"invoice_id": "INV-1009", "payee_id": "PY-04",
                                          "amount_cents": 1600000})["order_id"]     # AP-03 valid through 5
        env.call("submit_for_approval", {"order_id": oid})
        idle(env, 2)
        late_release(env, oid, k=3)                                        # accepted at tick 5, commits at 8
        idle(env, 3)
        self.assertEqual(env.orders[oid]["status"], "released")           # authorised in time
        self.assertEqual(env.approvals["AP-03"]["status"], "used")

    def test_acceptance_after_expiry_is_a_gateway_denial_invisible_to_the_core(self):
        env = fresh()
        oid = env.call("create_payment", {"invoice_id": "INV-1009", "payee_id": "PY-04",
                                          "amount_cents": 1600000, "client_ref": "c"})["order_id"]
        env.call("submit_for_approval", {"order_id": oid})
        idle(env, 3)                                                       # tick 6 > 5
        self.assertEqual(late_release(env, oid, key="r"), TIMEOUT_RESPONSE)
        op = list(env.ops.ops.values())[-1]
        self.assertEqual((op.state, op.detail["reason"]), ("denied", "approval_expired"))
        self.assertEqual(env.call("get_operation_status", {"client_ref": "r"})["status"], "not_found")
        self.assertEqual(env.late_queue, [])

    def test_defect_malformed_key_is_denied_at_the_gateway_as_on_the_immediate_path(self):
        outcomes = {}
        for world, kw in (("commit_response_lost", {}), ("late_commit", {"commit_delay_ticks": 2})):
            env = fresh()
            oid = approved(env)
            env.call("release_payment", {"order_id": oid, "client_ref": 123}, fault=world, **kw)
            idle(env, 3)
            op = [o for o in env.ops.ops.values() if o.tool == "release_payment"][0]
            outcomes[world] = (op.state, op.detail.get("reason"), env.orders[oid]["status"])
            self.assertEqual(env.late_queue, [])
        self.assertEqual(outcomes["late_commit"], outcomes["commit_response_lost"])
        self.assertEqual(outcomes["late_commit"], ("denied", "invalid_argument", "approved"))

    def test_instruction_altered_in_the_window_is_refused_at_commit(self):
        env = fresh()
        oid = approved(env)
        late_release(env, oid)
        late_op = list(env.ops.ops.values())[-1].op_id
        env.orders[oid]["amount_cents"] = 999999                          # simulated tampering
        idle(env, K)
        self.assertEqual(env.ops.ops[late_op].detail["reason"], "approval_mismatch")
        self.assertEqual(env.ledger.balance(RESERVED), 0)


class TestReconciliationAndAtomicity(unittest.TestCase):
    def test_reconcile_after_the_delayed_commit_settles(self):
        env = fresh()
        oid = approved(env)
        late_release(env, oid, key="r")
        idle(env, K + DELTA)
        line = next(l for l in env.call("list_statement_lines")["lines"] if l["reference"] == oid)
        self.assertTrue(env.call("reconcile_statement_line", {"line_id": line["line_id"], "order_id": oid})["ok"])
        self.assertEqual(env.violations(), [])
        self.assertEqual(sum(env.ledger.balances.values()), 0)

    def test_exception_inside_the_delayed_commit_rolls_back(self):
        env = fresh()
        oid = approved(env)
        late_release(env, oid)
        real = env._transition
        env._transition = lambda o, s: (_ for _ in ()).throw(RuntimeError("boom")) if s == "released" else real(o, s)
        idle(env, K - 1)
        with self.assertRaises(mer.EnvironmentFault):
            env.call("list_payees")
        self.assertEqual(env.ledger.balance(RESERVED), 0)
        self.assertEqual(env.orders[oid]["status"], "approved")
        self.assertEqual(list(env.ops.ops.values())[-1].state, "not_committed")


class TestDeterminism(unittest.TestCase):
    def _episode(self, seed):
        env = fresh(seed)
        oid = approved(env)
        late_release(env, oid, key="r")
        env.call("get_operation_status", {"client_ref": "r"})
        oid2 = approved(env)
        env.call("release_payment", {"order_id": oid2})
        idle(env, K + DELTA)
        return env

    def test_identical_seed_replays_exactly(self):
        a, b = self._episode(9), self._episode(9)
        self.assertEqual(a.snapshot_hash(), b.snapshot_hash())
        self.assertEqual(json.dumps(a.audit_log()), json.dumps(b.audit_log()))
        self.assertEqual(json.dumps(a.violations()), json.dumps(b.violations()))

    def test_other_seed_same_semantics(self):
        a, b = self._episode(9), self._episode(10)
        sem = lambda e: (e.snapshot()["balances"], [v["invariant"] for v in e.violations()],
                         [x["event"] for x in e.audit_log()])
        self.assertEqual(sem(a), sem(b))
        self.assertNotEqual(sorted(a.orders), sorted(b.orders))


if __name__ == "__main__":
    unittest.main()
