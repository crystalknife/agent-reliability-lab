"""Adversarial regression tests for Ledger + Meridian v0 (correctness audit, 2026-11-17).

Each class targets one audited property; tests named `test_defect_*` reproduce a confirmed defect.
"""

import json
import unittest

from minilab.bankops.envs import meridian as mer
from minilab.bankops.envs.meridian import AVAILABLE, CLEARING, RESERVED, TIMEOUT_RESPONSE, MeridianEnv
from minilab.bankops.ledger import ViolationLog

CREATE = {"invoice_id": "INV-1001", "payee_id": "PY-01", "amount_cents": 180000}


def fresh(seed=5):
    env = MeridianEnv()
    env.reset(seed)
    return env


def m4(env):
    return sorted(v["subject"] for v in env.violations() if v["invariant"] == "M4")


def structure(env):
    """Financial state without object names (ids differ legitimately when an extra request was issued)."""
    snap = env.snapshot()
    return {"balances": snap["balances"],
            "orders": sorted((o["status"], o["amount_cents"], o["invoice_id"]) for o in snap["orders"].values()),
            "approvals": sorted((a["kind"], a["status"], a["order_id"] is None) for a in snap["approvals"].values()),
            "matched_lines": sum(l["matched_order_id"] is not None for l in snap["lines"].values()),
            "released_today_cents": snap["released_today_cents"], "groups": len(env.ledger.groups)}


def state_projection(env):
    """Financial state only (no clock, ticks or op ids): what must be identical after a rollback."""
    snap = env.snapshot()
    return {"balances": snap["balances"],
            "orders": {k: (o["status"], o["amount_cents"], o["invoice_id"], o["approval_id"])
                       for k, o in snap["orders"].items()},
            "approvals": {k: (a["status"], a["order_id"]) for k, a in snap["approvals"].items()},
            "lines": {k: l["matched_order_id"] for k, l in snap["lines"].items()},
            "released_today_cents": snap["released_today_cents"],
            "groups": len(env.ledger.groups)}


def approved_order(env, invoice="INV-1001"):
    inv = env.invoices[invoice]
    oid = env.call("create_payment", {"invoice_id": invoice, "payee_id": inv["payee_id"],
                                      "amount_cents": inv["amount_cents"]})["order_id"]
    env.call("submit_for_approval", {"order_id": oid})
    return oid


class TestAtomicity(unittest.TestCase):  # audit item 1
    def test_defect_exception_inside_release_commit_rolls_back_everything(self):
        env = fresh()
        oid = approved_order(env)
        before = state_projection(env)
        real = env._transition

        def explode(order, to_state):
            if to_state == "released":   # after the ledger posting, before the order transition
                raise RuntimeError("simulated crash mid-commit")
            return real(order, to_state)
        env._transition = explode
        with self.assertRaises(mer.EnvironmentFault):
            env.call("release_payment", {"order_id": oid})
        self.assertEqual(state_projection(env), before)          # no half-applied ledger posting
        op = list(env.ops.ops.values())[-1]
        self.assertEqual((op.state, op.detail.get("reason")), ("not_committed", "rolled_back"))
        self.assertEqual(env.violations(), [])
        env._transition = real                                    # the bank keeps working afterwards
        self.assertTrue(env.call("release_payment", {"order_id": oid})["ok"])
        self.assertEqual(env.ledger.balance(RESERVED), 180000)

    def test_defect_exception_during_settlement_rolls_back(self):
        env = fresh()
        oid = approved_order(env)
        env.call("release_payment", {"order_id": oid})
        before = state_projection(env)
        real = env._transition
        env._transition = lambda o, s: (_ for _ in ()).throw(RuntimeError("boom")) if s == "settled" else real(o, s)
        for _ in range(2):
            env.call("list_payees")
        with self.assertRaises(mer.EnvironmentFault):
            env.call("list_payees")                               # settlement due on this tick
        self.assertEqual(state_projection(env), before)
        self.assertEqual(env.orders[oid]["status"], "released")

    def test_unexpected_planning_error_is_an_environment_fault(self):
        env = fresh()
        oid = approved_order(env)
        env._plan_cancel_payment = lambda args: {}["missing"]      # KeyError inside planning
        with self.assertRaises(mer.EnvironmentFault):
            env.call("cancel_payment", {"order_id": oid})
        self.assertEqual(list(env.ops.ops.values())[-1].state, "not_committed")


class TestDoubleEntry(unittest.TestCase):  # audit item 2
    def test_books_balance_after_every_operation(self):
        env = fresh()
        script = [("create_payment", dict(CREATE)), ("submit_for_approval", None), ("release_payment", None),
                  ("list_payees", {}), ("list_payees", {}), ("list_payees", {}), ("list_statement_lines", {})]
        oid = None
        for tool, args in script:
            out = env.call(tool, args if args is not None else {"order_id": oid})
            oid = oid or out.get("order_id")
            self.assertEqual(sum(env.ledger.balances.values()), 0, tool)         # global conservation
            self.assertEqual({k: v for k, v in env.ledger.recomputed().items() if v},
                             {k: v for k, v in env.ledger.balances.items() if v}, tool)
        self.assertEqual(env.ledger.balance(CLEARING), 75000 + 180000)


class TestIdempotencyKeys(unittest.TestCase):  # audit item 3
    def test_identical_payload_with_reordered_keys_replays(self):
        env = fresh()
        a = env.call("create_payment", {**CREATE, "client_ref": "k"})
        b = env.call("create_payment", {"amount_cents": 180000, "payee_id": "PY-01", "invoice_id": "INV-1001",
                                        "client_ref": "k"})
        self.assertEqual(a, b)
        self.assertEqual(len(env.orders), 2)                      # history order + one draft

    def test_defect_type_changed_payload_is_a_conflict_not_a_replay(self):
        env = fresh()
        env.call("create_payment", {**CREATE, "client_ref": "k"})
        r = env.call("create_payment", {**CREATE, "amount_cents": 180000.0, "client_ref": "k"})
        self.assertEqual(r.get("reason"), "client_ref_conflict")  # 180000.0 is not the same request

    def test_same_key_on_another_tool_is_a_conflict(self):
        env = fresh()
        oid = env.call("create_payment", {**CREATE, "client_ref": "k"})["order_id"]
        r = env.call("submit_for_approval", {"order_id": oid, "client_ref": "k"})
        self.assertEqual(r["reason"], "client_ref_conflict")
        self.assertEqual(env.orders[oid]["status"], "draft")

    def test_key_of_a_denied_request_can_be_reused(self):
        env = fresh()
        oid = env.call("create_payment", {**CREATE})["order_id"]
        self.assertEqual(env.call("release_payment", {"order_id": oid, "client_ref": "k"})["reason"],
                         "invalid_transition")
        env.call("submit_for_approval", {"order_id": oid})
        self.assertTrue(env.call("release_payment", {"order_id": oid, "client_ref": "k"})["ok"])


class TestApprovals(unittest.TestCase):  # audit item 4
    def test_lost_submit_retried_with_key_binds_one_approval(self):
        for world in mer.FAULTS:
            with self.subTest(world=world):
                env = fresh()
                oid = env.call("create_payment", dict(CREATE))["order_id"]
                env.call("submit_for_approval", {"order_id": oid, "client_ref": "s"}, fault=world)
                env.call("submit_for_approval", {"order_id": oid, "client_ref": "s"})
                autos = [a for a in env.approvals.values() if a["kind"] == "auto"]
                self.assertEqual(len(autos), 1)
                self.assertEqual(env.orders[oid]["status"], "approved")

    def test_expiry_boundary_and_released_approval_replay(self):
        env = fresh()
        oid = env.call("create_payment", {"invoice_id": "INV-1009", "payee_id": "PY-04",
                                          "amount_cents": 1600000})["order_id"]      # AP-03 expires at tick 5
        env.call("submit_for_approval", {"order_id": oid})
        env.call("list_payees")
        env.call("list_payees")                                   # clock 4
        r = env.call("release_payment", {"order_id": oid, "client_ref": "r"})       # tick 5 == expiry: valid
        self.assertTrue(r["ok"])
        for _ in range(3):
            env.call("list_payees")
        replay = env.call("release_payment", {"order_id": oid, "client_ref": "r"})  # after expiry: replay only
        self.assertEqual(replay, r)
        self.assertEqual(env.approvals["AP-03"]["status"], "used")

    def test_preapproval_cannot_serve_two_orders(self):
        env = fresh()
        a = approved_order(env, "INV-1004")
        b = env.call("create_payment", {"invoice_id": "INV-1004", "payee_id": "PY-03",
                                        "amount_cents": 1250000})["order_id"]
        self.assertEqual(env.call("submit_for_approval", {"order_id": b})["status"], "pending_approval")
        env.call("release_payment", {"order_id": a})
        self.assertEqual(env.call("release_payment", {"order_id": b})["reason"], "invalid_transition")


class TestFaultMatrix(unittest.TestCase):  # audit item 5: every write tool, both hidden worlds
    def _setups(self):
        def create(env):
            return "create_payment", dict(CREATE)

        def submit(env):
            return "submit_for_approval", {"order_id": env.call("create_payment", dict(CREATE))["order_id"]}

        def release(env):
            return "release_payment", {"order_id": approved_order(env)}

        def cancel(env):
            return "cancel_payment", {"order_id": env.call("create_payment", dict(CREATE))["order_id"]}

        def reconcile(env):
            oid = approved_order(env)
            env.call("release_payment", {"order_id": oid})
            for _ in range(3):
                env.call("list_payees")
            line = next(l for l in env.lines.values() if l["reference"] == oid)
            return "reconcile_statement_line", {"line_id": line["line_id"], "order_id": oid}
        return {"create": create, "submit": submit, "release": release, "cancel": cancel, "reconcile": reconcile}

    def test_identical_observation_hidden_effect_and_single_effect_after_keyed_retry(self):
        for name, setup in self._setups().items():
            with self.subTest(tool=name):
                baseline = fresh()
                tool, args = setup(baseline)
                baseline.call(tool, {**args, "client_ref": "x"})
                expected = structure(baseline)
                observations = {}
                for world in mer.FAULTS:
                    env = fresh()
                    setup(env)
                    observations[world] = env.call(tool, {**args, "client_ref": "x"}, fault=world)
                    status = env.call("get_operation_status", {"client_ref": "x"})["status"]
                    self.assertEqual(status, "processed" if world == "commit_response_lost" else "not_found")
                    env.call(tool, {**args, "client_ref": "x"})       # same-key retry
                    self.assertEqual(structure(env), expected, world)  # the effect happened exactly once
                self.assertEqual(observations["commit_response_lost"], observations["fail_before_commit"])
                self.assertEqual(observations["fail_before_commit"], TIMEOUT_RESPONSE)


class TestNoSideChannels(unittest.TestCase):  # audit item 6
    def test_defect_later_observations_do_not_reveal_a_lost_create(self):
        outs = {}
        for world in mer.FAULTS:
            env = fresh()
            env.call("create_payment", dict(CREATE), fault=world)
            # observations that are NOT reconciliation reads must not differ between the hidden worlds
            outs[world] = [env.call("create_payment", {"invoice_id": "INV-1002", "payee_id": "PY-02",
                                                       "amount_cents": 240000}),
                           env.call("list_invoices"), env.call("list_payees"), env.call("search_policy", {})]
        self.assertEqual(outs["commit_response_lost"], outs["fail_before_commit"])

    def test_lost_release_is_invisible_outside_reconciliation_reads(self):
        outs = {}
        for world in mer.FAULTS:
            env = fresh()
            oid = approved_order(env)
            env.call("release_payment", {"order_id": oid}, fault=world)
            other = approved_order(env, "INV-1002")
            outs[world] = [env.call("list_invoices"), env.call("search_policy", {"query": "payment"}),
                           env.call("get_invoice", {"invoice_id": "INV-1001"}),
                           env.call("release_payment", {"order_id": other})]
        self.assertEqual(outs["commit_response_lost"], outs["fail_before_commit"])


class TestViolationLog(unittest.TestCase):  # audit item 7
    def test_defect_info_fields_cannot_overwrite_record_metadata(self):
        vl = ViolationLog()
        vl.update({("M4", "x"): {"kind": "procedural", "opened_tick": 999, "invariant": "fake"}}, 3, "op")
        self.assertEqual((vl.records[0]["invariant"], vl.records[0]["opened_tick"]), ("M4", 3))

    def test_defect_every_excess_payment_gets_its_own_record(self):
        env = fresh()
        orders = [env.call("create_payment", {"invoice_id": "INV-1006", "payee_id": "PY-05",
                                              "amount_cents": 150000})["order_id"] for _ in range(3)]
        for o in orders:
            env.call("submit_for_approval", {"order_id": o})
        for o in orders:
            env.call("release_payment", {"order_id": o})
        self.assertEqual(len(m4(env)), 2)                          # two excess payments, two records
        for _ in range(3):
            env.call("list_payees")                                # all returned
        self.assertEqual(env.active_violations(), [])
        self.assertEqual(len([v for v in env.violations() if v["invariant"] == "M4"]), 2)

    def test_records_are_copies(self):
        env = fresh()
        approved = approved_order(env)
        env.call("release_payment", {"order_id": approved})
        dup = approved_order(env)
        env.call("release_payment", {"order_id": dup})
        env.violations()[0]["closed_tick"] = 1                     # tampering with a copy
        self.assertIsNone(env.violations()[0]["closed_tick"])


class TestSettlementAndReconciliation(unittest.TestCase):  # audit item 8
    def test_defect_one_order_matched_by_two_lines_is_detected(self):
        env = fresh()
        a, b = approved_order(env, "INV-1001"), approved_order(env, "INV-1003")   # same payee and amount
        env.call("release_payment", {"order_id": a})
        env.call("release_payment", {"order_id": b})
        for _ in range(3):
            env.call("list_payees")
        la = next(l for l in env.lines.values() if l["reference"] == a)
        lb = next(l for l in env.lines.values() if l["reference"] == b)
        env.call("reconcile_statement_line", {"line_id": la["line_id"], "order_id": a})
        env.call("reconcile_statement_line", {"line_id": lb["line_id"], "order_id": a})   # same order twice
        self.assertIn(("P-RECON-DUP", a), {(v["invariant"], v["subject"]) for v in env.violations()})

    def test_cancel_return_and_settle_keep_books_consistent(self):
        env = fresh()
        keep = approved_order(env, "INV-1001")
        returned = approved_order(env, "INV-1006")
        cancelled = approved_order(env, "INV-1002")
        env.call("cancel_payment", {"order_id": cancelled})
        env.call("release_payment", {"order_id": keep})
        env.call("release_payment", {"order_id": returned})
        for _ in range(4):
            env.call("list_payees")
        opening = 5000000 - 75000
        self.assertEqual(env.ledger.balance(AVAILABLE), opening - 180000)
        self.assertEqual(env.ledger.balance(RESERVED), 0)
        self.assertEqual(sum(env.ledger.balances.values()), 0)
        self.assertEqual([v for v in env.violations() if v["kind"] == "hard"], [])
        self.assertEqual(env.released_today_cents, 180000 + 150000)  # returned releases still count (§4.2)


class TestDeterminism(unittest.TestCase):  # audit item 9
    def test_defect_invalid_runner_call_does_not_mutate_state(self):
        env = fresh()
        before = (env.clock, env.snapshot_hash())
        with self.assertRaises(ValueError):
            env.call("list_invoices", fault="commit_response_lost")
        with self.assertRaises(ValueError):
            env.call("release_payment", {"order_id": "x"}, fault="chaos")
        self.assertEqual((env.clock, env.snapshot_hash()), before)

    def test_identical_episodes_replay_bit_for_bit(self):
        def run():
            env = fresh(9)
            oid = approved_order(env)
            env.call("release_payment", {"order_id": oid, "client_ref": "r"}, fault="commit_response_lost")
            env.call("release_payment", {"order_id": oid, "client_ref": "r"})
            for _ in range(3):
                env.call("list_statement_lines")
            return env.snapshot_hash(), json.dumps(env.audit_log()), json.dumps(env.violations())
        self.assertEqual(run(), run())


if __name__ == "__main__":
    unittest.main()
