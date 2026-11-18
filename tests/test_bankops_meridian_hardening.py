"""Design-hardening tests for Meridian v0: approval binding, expiry boundary, seeds (2026-11-17).

`test_defect_*` reproduces a defect confirmed during this pass; the rest pin down explicit rules.
"""

import copy
import json
import unittest

from minilab.bankops.envs import meridian as mer
from minilab.bankops.envs.meridian import FIXTURE, RESERVED, MeridianEnv

BASE = json.loads(FIXTURE.read_text(encoding="utf-8"))


def twin_invoice_fixture():
    """Two invoices from one payee for the same amount, above the auto threshold; one approval for INV-A."""
    fx = copy.deepcopy(BASE)
    fx["invoices"] += [
        {"id": "INV-A", "payee_id": "PY-03", "amount_cents": 900000, "due_tick": 0, "reference": "CO-A"},
        {"id": "INV-B", "payee_id": "PY-03", "amount_cents": 900000, "due_tick": 0, "reference": "CO-B"}]
    fx["approvals"].append({"id": "AP-A", "payee_id": "PY-03", "amount_cents": 900000, "invoice_id": "INV-A",
                            "expires_tick": None})
    return fx


def create(env, invoice, payee, amount):
    return env.call("create_payment", {"invoice_id": invoice, "payee_id": payee, "amount_cents": amount})["order_id"]


class TestApprovalBinding(unittest.TestCase):
    def setUp(self):
        self.env = MeridianEnv(twin_invoice_fixture())
        self.env.reset(3)

    def test_defect_approval_for_one_invoice_cannot_approve_another(self):
        b = create(self.env, "INV-B", "PY-03", 900000)                 # same payee and amount, other invoice
        self.assertEqual(self.env.call("submit_for_approval", {"order_id": b})["status"], "pending_approval")
        self.assertEqual(self.env.approvals["AP-A"]["status"], "available")
        a = create(self.env, "INV-A", "PY-03", 900000)
        self.assertEqual(self.env.call("submit_for_approval", {"order_id": a})["status"], "approved")

    def test_approval_cannot_serve_a_second_identical_instruction(self):
        a1 = create(self.env, "INV-A", "PY-03", 900000)
        a2 = create(self.env, "INV-A", "PY-03", 900000)               # otherwise identical payment
        self.env.call("submit_for_approval", {"order_id": a1})
        self.assertEqual(self.env.call("submit_for_approval", {"order_id": a2})["status"], "pending_approval")
        self.assertTrue(self.env.call("release_payment", {"order_id": a1})["ok"])
        self.assertEqual(self.env.call("release_payment", {"order_id": a2})["reason"], "invalid_transition")

    def test_defect_altered_instruction_is_refused_at_release(self):
        a = create(self.env, "INV-A", "PY-03", 900000)
        self.env.call("submit_for_approval", {"order_id": a})
        for field, value in (("amount_cents", 900001), ("payee_id", "PY-01"), ("invoice_id", "INV-B")):
            with self.subTest(field=field):
                env = copy.deepcopy(self.env)
                env.orders[a][field] = value                            # instruction changed after approval
                r = env.call("release_payment", {"order_id": a})
                self.assertEqual(r["reason"], "approval_mismatch")
                self.assertEqual(env.ledger.balance(RESERVED), 0)

    def test_defect_instruction_tampered_after_release_is_a_hard_violation(self):
        a = create(self.env, "INV-A", "PY-03", 900000)
        self.env.call("submit_for_approval", {"order_id": a})
        self.env.call("release_payment", {"order_id": a})
        self.env.orders[a]["invoice_id"] = "INV-B"                      # simulated environment bug
        create(self.env, "INV-1001", "PY-01", 180000)                   # next commit re-checks invariants
        self.assertIn(("M3", a), {(v["invariant"], v["subject"]) for v in self.env.violations()})

    def test_cancel_releases_the_binding_for_a_new_instruction(self):
        a1 = create(self.env, "INV-A", "PY-03", 900000)
        self.env.call("submit_for_approval", {"order_id": a1})
        self.env.call("cancel_payment", {"order_id": a1})
        appr = self.env.approvals["AP-A"]
        self.assertEqual((appr["status"], appr["order_id"], appr.get("instruction_sha256")), ("available", None, None))
        a2 = create(self.env, "INV-A", "PY-03", 900000)
        self.assertEqual(self.env.call("submit_for_approval", {"order_id": a2})["status"], "approved")
        self.assertTrue(self.env.call("release_payment", {"order_id": a2})["ok"])

    def test_auto_approvals_are_instruction_bound_too(self):
        env = MeridianEnv()
        env.reset(3)
        o = create(env, "INV-1001", "PY-01", 180000)
        env.call("submit_for_approval", {"order_id": o})
        appr = env.approvals[env.orders[o]["approval_id"]]
        self.assertEqual(appr["instruction"], {"order_id": o, "invoice_id": "INV-1001", "payee_id": "PY-01",
                                               "amount_cents": 180000})


class TestApprovalExpiryBoundary(unittest.TestCase):
    """Rule: an approval with expires_tick = T is valid at every tick t <= T and invalid from T + 1."""

    def _run(self, idle_reads):
        env = MeridianEnv()
        env.reset(3)
        o = create(env, "INV-1009", "PY-04", 1600000)                  # AP-03: expires_tick 5
        env.call("submit_for_approval", {"order_id": o})              # tick 2
        for _ in range(idle_reads):
            env.call("list_payees")
        return env, env.call("release_payment", {"order_id": o})      # tick 3 + idle_reads

    def test_valid_at_the_expiry_tick(self):
        env, r = self._run(2)
        self.assertEqual(env.clock, 5)
        self.assertTrue(r["ok"])

    def test_invalid_one_tick_after(self):
        env, r = self._run(3)
        self.assertEqual(env.clock, 6)
        self.assertEqual(r["reason"], "approval_expired")

    def test_binding_obeys_the_same_rule(self):
        for idle, submit_tick, expected in ((3, 5, "approved"), (4, 6, "pending_approval")):
            with self.subTest(submit_tick=submit_tick):
                env = MeridianEnv()
                env.reset(3)
                for _ in range(idle):
                    env.call("list_payees")
                o = create(env, "INV-1009", "PY-04", 1600000)          # tick idle + 1
                r = env.call("submit_for_approval", {"order_id": o})  # tick idle + 2
                self.assertEqual((env.clock, r["status"]), (submit_tick, expected))

    def test_rule_is_exposed_as_one_function(self):
        appr = {"expires_tick": 5}
        self.assertTrue(mer.approval_valid_at(appr, 5))
        self.assertFalse(mer.approval_valid_at(appr, 6))
        self.assertTrue(mer.approval_valid_at({"expires_tick": None}, 10 ** 9))


class TestSeeds(unittest.TestCase):
    SCRIPT = ("INV-1001", "INV-1006", "INV-1004")

    def _episode(self, seed):
        env = MeridianEnv()
        env.reset(seed)
        orders = []
        for inv in self.SCRIPT:
            i = env.invoices[inv]
            o = create(env, inv, i["payee_id"], i["amount_cents"])
            env.call("submit_for_approval", {"order_id": o})
            env.call("release_payment", {"order_id": o, "client_ref": f"r-{inv}"}, fault="commit_response_lost")
            env.call("release_payment", {"order_id": o, "client_ref": f"r-{inv}"})
            orders.append(o)
        for _ in range(4):
            env.call("list_payees")
        return env, orders

    def _semantics(self, env):
        return {"balances": env.snapshot()["balances"],
                "statuses": sorted((o["invoice_id"], o["status"]) for o in env.orders.values()),
                "violations": sorted((v["invariant"], v["kind"]) for v in env.violations()),
                "events": [e["event"] for e in env.audit_log()]}

    def test_seed_changes_identifiers_and_presentation_but_not_semantics(self):
        (e1, o1), (e2, o2) = self._episode(1), self._episode(2)
        self.assertEqual(self._semantics(e1), self._semantics(e2))
        self.assertTrue(set(o1).isdisjoint(o2))                          # identifiers differ
        lists = lambda e: [[x["id"] for x in e.call("list_invoices")["invoices"]],
                           [p["id"] for p in e.call("list_payees")["payees"]]]
        self.assertNotEqual(lists(e1), lists(e2))                        # presentation order varies
        self.assertEqual(sorted(lists(e1)[0]), sorted(lists(e2)[0]))     # same content

    def test_identical_seed_replays_exactly(self):
        (e1, o1), (e2, o2) = self._episode(7), self._episode(7)
        self.assertEqual(o1, o2)
        self.assertEqual(e1.snapshot_hash(), e2.snapshot_hash())
        self.assertEqual(json.dumps(e1.audit_log()), json.dumps(e2.audit_log()))
        self.assertEqual(e1.call("list_invoices"), e2.call("list_invoices"))

    def test_task_facing_entities_are_seed_independent(self):
        envs = [MeridianEnv() for _ in range(3)]
        for seed, env in enumerate(envs):
            env.reset(seed)
        self.assertEqual(len({json.dumps(sorted(e.invoices)) for e in envs}), 1)  # "Pay INV-1001" stays valid
        self.assertEqual(len({json.dumps(e.params, sort_keys=True) for e in envs}), 1)


if __name__ == "__main__":
    unittest.main()
