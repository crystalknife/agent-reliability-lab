"""BankOps ledger, operation lifecycle and violation log (environment-independent units)."""

import unittest

from minilab.bankops.ledger import (AUTHORIZED, COMMITTED, DENIED, EXECUTED, NOT_COMMITTED, REPLAYED, Ledger,
                                    LedgerError, LifecycleError, OperationLog, ViolationLog)


class TestLedger(unittest.TestCase):
    def test_zero_sum_groups_apply_and_recompute(self):
        led = Ledger()
        led.post("G1", "op1", 1, [("a", 500), ("b", -500)])
        led.post("G2", "op2", 2, [("a", -200), ("c", 200)])
        self.assertEqual((led.balance("a"), led.balance("b"), led.balance("c")), (300, -500, 200))
        self.assertEqual(led.recomputed(), led.balances)
        self.assertEqual([g["group_id"] for g in led.postings_for("op2")], ["G2"])

    def test_rejected_group_changes_nothing(self):
        led = Ledger()
        led.post("G1", "op1", 1, [("a", 100), ("b", -100)])
        before = (dict(led.balances), len(led.groups))
        for bad in ([("a", 100), ("b", -99)], [], [("a", 0), ("b", 0)], [("a", 1.5), ("b", -1.5)],
                    [("a", True), ("b", -1)]):
            with self.subTest(bad=bad), self.assertRaises(LedgerError):
                led.post("GX", "opx", 2, bad)
        self.assertEqual((dict(led.balances), len(led.groups)), before)


class TestOperationLifecycle(unittest.TestCase):
    def test_legal_paths(self):
        log = OperationLog()
        for path in ([AUTHORIZED, EXECUTED, COMMITTED], [DENIED], [NOT_COMMITTED], [REPLAYED]):
            op = log.request(f"op{len(log.ops)}", "t", {}, None, None, 1)
            for s in path:
                log.advance(op, s, 1)
            log.respond(op, True, {"ok": True}, 1)
        self.assertEqual(log.events[0]["event"], "requested")

    def test_illegal_transitions_are_rejected(self):
        log = OperationLog()
        op = log.request("op1", "t", {}, None, None, 1)
        with self.assertRaises(LifecycleError):
            log.advance(op, COMMITTED, 1)          # cannot commit without authorisation and execution
        with self.assertRaises(LifecycleError):
            log.respond(op, True, {}, 1)           # cannot respond before processing ends
        log.advance(op, DENIED, 1)
        with self.assertRaises(LifecycleError):
            log.advance(op, AUTHORIZED, 1)         # terminal
        log.respond(op, False, {}, 1)
        with self.assertRaises(LifecycleError):
            log.respond(op, True, {}, 1)           # exactly one response outcome


class TestViolationLog(unittest.TestCase):
    def test_reversed_breach_stays_recorded(self):
        vl = ViolationLog()
        vl.update({("M4", "INV-1"): {"kind": "procedural"}}, 3, "op-a")
        vl.update({("M4", "INV-1"): {"kind": "procedural"}}, 4, "op-b")   # still active: no duplicate record
        self.assertEqual(len(vl.records), 1)
        vl.update({}, 7, "system:settle")
        self.assertEqual(vl.active(), [])
        rec = vl.records[0]
        self.assertEqual((rec["opened_tick"], rec["closed_tick"], rec["closed_by"]), (3, 7, "system:settle"))


if __name__ == "__main__":
    unittest.main()
