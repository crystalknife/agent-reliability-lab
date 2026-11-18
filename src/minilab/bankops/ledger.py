"""Double-entry ledger, operation lifecycle and invariant-violation log for BankOps environments.

Everything here is deterministic and agent-invisible: environments use it to record what really
happened (readiness doc §5.1), and the grader reads it. Amounts are integer cents.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


class LedgerError(Exception):
    """An environment bug: an unbalanced or malformed posting group. Never caused by the agent."""


class LifecycleError(Exception):
    """An environment bug: an illegal operation-lifecycle transition."""


class Ledger:
    """Accounts with integer-cent balances; every change is a zero-sum posting group (invariant M1/I1)."""

    def __init__(self):
        self.balances: Dict[str, int] = {}
        self.groups: List[Dict[str, Any]] = []

    def post(self, group_id: str, op_id: str, tick: int, postings: List[Tuple[str, int]]) -> None:
        if not postings:
            raise LedgerError(f"{group_id}: empty posting group")
        for account, cents in postings:
            if not isinstance(account, str) or not isinstance(cents, int) or isinstance(cents, bool) or cents == 0:
                raise LedgerError(f"{group_id}: malformed posting {account!r} {cents!r}")
        if sum(c for _, c in postings) != 0:
            raise LedgerError(f"{group_id}: postings do not sum to zero")
        # validated first, applied second: a rejected group changes nothing
        for account, cents in postings:
            self.balances[account] = self.balances.get(account, 0) + cents
        self.groups.append({"group_id": group_id, "op_id": op_id, "tick": tick, "postings": list(postings)})

    def balance(self, account: str) -> int:
        return self.balances.get(account, 0)

    def recomputed(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for g in self.groups:
            for account, cents in g["postings"]:
                out[account] = out.get(account, 0) + cents
        return out

    def postings_for(self, op_id: str) -> List[Dict[str, Any]]:
        return [g for g in self.groups if g["op_id"] == op_id]


# Operation processing states and the only legal transitions between them.
REQUESTED, AUTHORIZED, DENIED, EXECUTED, COMMITTED, NOT_COMMITTED, REPLAYED, QUEUED = (
    "requested", "authorized", "denied", "executed", "committed", "not_committed", "replayed", "queued")
_NEXT = {
    REQUESTED: {AUTHORIZED, DENIED, NOT_COMMITTED, REPLAYED},
    AUTHORIZED: {EXECUTED, QUEUED},          # QUEUED = authorised, awaiting a delayed commit
    QUEUED: {EXECUTED, DENIED, REPLAYED},    # resolved when the delayed commit falls due
    EXECUTED: {COMMITTED, NOT_COMMITTED},    # NOT_COMMITTED here = rolled back after an internal error
}
_TERMINAL = {DENIED, COMMITTED, NOT_COMMITTED, REPLAYED}
_RESPONDABLE = _TERMINAL | {QUEUED}          # a delayed commit's response is lost at acceptance


@dataclass
class Operation:
    op_id: str
    tool: str
    args: Dict[str, Any]
    client_ref: Optional[str]
    economic_key: Optional[str]
    tick: int
    state: str = REQUESTED
    response_state: Optional[str] = None      # "delivered" | "dropped"
    response: Optional[Dict[str, Any]] = None  # what the agent received (or would have)
    detail: Dict[str, Any] = field(default_factory=dict)


class OperationLog:
    """Operations plus an append-only audit event stream (the agent never sees it)."""

    def __init__(self):
        self.ops: Dict[str, Operation] = {}
        self.events: List[Dict[str, Any]] = []

    def _event(self, tick: int, op_id: str, event: str, **fields) -> None:
        self.events.append({"seq": len(self.events) + 1, "tick": tick, "op_id": op_id, "event": event, **fields})

    def request(self, op_id, tool, args, client_ref, economic_key, tick, **fields) -> Operation:
        op = Operation(op_id, tool, dict(args), client_ref, economic_key, tick)
        self.ops[op_id] = op
        self._event(tick, op_id, REQUESTED, tool=tool, args=copy.deepcopy(dict(args)), client_ref=client_ref,
                    economic_key=economic_key, **fields)
        return op

    def advance(self, op: Operation, new_state: str, tick: int, **fields) -> None:
        if new_state not in _NEXT.get(op.state, set()):
            raise LifecycleError(f"{op.op_id}: illegal transition {op.state} -> {new_state}")
        op.state = new_state
        op.detail.update(fields)
        self._event(tick, op.op_id, new_state, **fields)

    def respond(self, op: Operation, delivered: bool, response: Dict[str, Any], tick: int, **fields) -> None:
        if op.state not in _RESPONDABLE:
            raise LifecycleError(f"{op.op_id}: cannot respond before processing ends (state {op.state})")
        if op.response_state is not None:
            raise LifecycleError(f"{op.op_id}: response already {op.response_state}")
        op.response_state = "delivered" if delivered else "dropped"
        op.response = response
        # the bank's actual response is recorded even when the agent never receives it (dropped)
        self._event(tick, op.op_id, "response_delivered" if delivered else "response_dropped",
                    response=copy.deepcopy(response), **fields)

    def system(self, tick: int, ref: str, event: str, **fields) -> None:
        self._event(tick, ref, event, actor="system", **fields)

    def agent(self, tick: int, event: str, **fields) -> None:
        """An agent action that is not an operation (e.g. a call to a tool that does not exist)."""
        self._event(tick, "agent", event, actor="agent", **fields)


class ViolationLog:
    """Invariant violations with open/close ticks. Records are never deleted, so a breach that is
    later reversed (e.g. a duplicate payment that is returned) stays visible as a closed record."""

    def __init__(self):
        self.records: List[Dict[str, Any]] = []
        self._active: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def update(self, current: Dict[Tuple[str, str], Dict[str, Any]], tick: int, cause: str) -> None:
        for key, info in current.items():
            if key not in self._active:
                # info first: it can never overwrite the record's identity or timing fields
                rec = {**info, "invariant": key[0], "subject": key[1], "opened_tick": tick, "opened_by": cause,
                       "closed_tick": None, "closed_by": None}
                self.records.append(rec)
                self._active[key] = rec
        for key in [k for k in self._active if k not in current]:
            rec = self._active.pop(key)
            rec["closed_tick"], rec["closed_by"] = tick, cause

    def active(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self._active.values()]
