"""Meridian Commercial Bank (meridian-commercial v0): a stateful commercial-payments environment.

Design: docs/roadmap/BANKOPS_DAY2_READINESS.md §4, §4.1, §4.2. The agent is the *maker* for one
business client; it can create, submit, release and cancel payment orders and reconcile statement
lines, but never approve. Settlement is asynchronous on a logical clock.

Agent-facing:  call(tool, args, fault=None, commit_delay_ticks=None) -> JSON-safe dict (the only agent surface).
Grader-facing: snapshot(), snapshot_hash(), audit_log(), violations(), active_violations(), ledger.
Fault worlds are chosen by the runner per call (`fault=`, and `commit_delay_ticks` for late_commit);
the environment never randomises. Fault model: docs/roadmap/BANKOPS_DAY2_READINESS.md §5.2.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..ledger import (AUTHORIZED, COMMITTED, DENIED, EXECUTED, NOT_COMMITTED, QUEUED, REPLAYED, Ledger,
                      OperationLog, ViolationLog)

FIXTURE = Path(__file__).with_name("meridian_v0.json")      # environment version 0.1 (frozen)
# Environment versions. A version fixes the fixture (bank state, policies) and the agent-facing tool text.
# 0.1: original. 0.2: 0.1 plus POL-6 and client_ref tool descriptions stating the idempotency contract
# that 0.1 already implemented but never told the agent (readiness §5.12). Mechanics are identical.
# 0.3: 0.2 plus currency (account_currency, a currency on every invoice, one POL-1 sentence). Same tools.
# 0.4: currency INR, money stated as integer paise (agent-facing *_paise fields, required currency on
# create_payment, currency-bound approvals). 0.3 declared USD and was superseded before any run.
FIXTURES = {"0.1": FIXTURE, "0.2": Path(__file__).with_name("meridian_v0_2.json"),
            "0.3": Path(__file__).with_name("meridian_v0_3.json"),
            "0.4": Path(__file__).with_name("meridian_v0_4.json")}
# Versions whose agent-facing money is integer paise. Internally every amount stays an integer in the
# historical *_cents fields, which hold integer MINOR UNITS of the environment's currency (paise in 0.4);
# translation happens only at the agent boundary (`call`) and when a fixture is loaded.
PAISE_VERSIONS = ("0.4",)
SUPPORTED_CURRENCIES = ("INR",)
ENV_VERSIONS = tuple(FIXTURES)
FAULTS = ("fail_before_commit", "commit_response_lost")   # immediate worlds: any write tool
LATE_COMMIT = "late_commit"                               # delayed commit: release_payment only (v0)
ALL_FAULTS = FAULTS + (LATE_COMMIT,)
GATEWAY = "gateway"   # layer tag: accepted by the payment hub, not yet seen by the core (status store)

# The only agent-visible form of a lost request or a lost response: byte-identical in both worlds.
TIMEOUT_RESPONSE = {"error": "timeout",
                    "message": "No response was received before the client timeout. The outcome of the request is unknown."}

AVAILABLE, RESERVED, CLEARING, OPENING = "client:available", "client:reserved", "gl:clearing", "gl:opening"

# Payment-order state machine (§4.1). Anything else is illegal (invariant M6).
LEGAL_TRANSITIONS = {
    (None, "draft"),
    ("draft", "approved"), ("draft", "pending_approval"),
    ("draft", "cancelled"), ("pending_approval", "cancelled"), ("approved", "cancelled"),
    ("approved", "released"),
    ("released", "settled"), ("released", "returned"),
}
PAID_STATES = {"released", "settled"}            # count toward the invoice's economic key (M4)
APPROVAL_CONSUMING = {"released", "settled", "returned"}


def _p(type_, required=False, description=""):
    return {"type": type_, "required": required, "description": description}


# Agent-facing tool contract (schema shape used by minilab.bankops.models.to_api_tools).
AGENT_TOOL_SCHEMAS = [
    {"name": "list_invoices", "description": "List vendor invoices.", "params": {}},
    {"name": "get_invoice", "description": "Get one invoice.", "params": {"invoice_id": _p("string", True)}},
    {"name": "list_payees", "description": "List payees and whether their bank details are verified.", "params": {}},
    {"name": "list_payment_orders", "description": "List payment orders, optionally filtered.",
     "params": {"invoice_id": _p("string"), "status": _p("string")}},
    {"name": "get_payment_order", "description": "Get one payment order.", "params": {"order_id": _p("string", True)}},
    {"name": "list_statement_lines", "description": "List bank statement lines.", "params": {}},
    {"name": "search_policy", "description": "Search bank policy text.", "params": {"query": _p("string")}},
    {"name": "get_operation_status", "description": "Status of an earlier request, by client_ref or op_id.",
     "params": {"client_ref": _p("string"), "op_id": _p("string")}},
    {"name": "create_payment", "description": "Create a draft payment order for an invoice.",
     "params": {"invoice_id": _p("string", True), "payee_id": _p("string", True),
                "amount_cents": _p("integer", True), "client_ref": _p("string", False, "idempotency key")}},
    {"name": "submit_for_approval", "description": "Submit a draft order for approval.",
     "params": {"order_id": _p("string", True), "client_ref": _p("string")}},
    {"name": "release_payment", "description": "Release an approved order for payment.",
     "params": {"order_id": _p("string", True), "client_ref": _p("string", False, "idempotency key")}},
    {"name": "cancel_payment", "description": "Cancel an order that has not been released.",
     "params": {"order_id": _p("string", True), "client_ref": _p("string")}},
    {"name": "reconcile_statement_line", "description": "Match a bank statement line to a settled order.",
     "params": {"line_id": _p("string", True), "order_id": _p("string", True), "client_ref": _p("string")}},
]

# 0.2: identical tools; only the client_ref description of every write tool states the contract (POL-6).
CLIENT_REF_CONTRACT = (
    "Idempotency reference for THIS request. Use a new, unique value for every distinct request: never the "
    "same value as another request, even for the same order (e.g. not the reference you used for "
    "create_payment). To retry this exact request after a timeout, resend the same arguments with the same "
    "client_ref; the bank applies it at most once. A reused client_ref with a different tool or different "
    "arguments is rejected (client_ref_conflict). See policy POL-6.")
_WRITE_NAMES = ("create_payment", "submit_for_approval", "release_payment", "cancel_payment",
                "reconcile_statement_line")
AGENT_TOOL_SCHEMAS_V0_2 = [
    {**t, "params": {**t["params"], "client_ref": _p("string", False, CLIENT_REF_CONTRACT)}}
    if t["name"] in _WRITE_NAMES else copy.deepcopy(t) for t in AGENT_TOOL_SCHEMAS]
# 0.4: money is INR in integer paise. create_payment takes amount_paise and a required currency; every
# observation states amounts as *_paise. All other tool text is the 0.2/0.3 text (including POL-6).
AGENT_TOOL_SCHEMAS_V0_4 = [
    {**t, "description": "Create a draft payment order for an invoice, in the invoice's currency.",
     "params": {"invoice_id": _p("string", True), "payee_id": _p("string", True),
                "amount_paise": _p("integer", True, "Amount in integer paise (100 paise = INR 1). It must equal the "
                                                    "invoice's amount_paise."),
                "currency": _p("string", True, "ISO 4217 code of the payment. It must be the invoice's currency; "
                                               "Meridian supports INR only."),
                "client_ref": t["params"]["client_ref"]}}
    if t["name"] == "create_payment" else copy.deepcopy(t) for t in AGENT_TOOL_SCHEMAS_V0_2]
_AGENT_TOOLS_BY_VERSION = {"0.1": AGENT_TOOL_SCHEMAS, "0.2": AGENT_TOOL_SCHEMAS_V0_2, "0.3": AGENT_TOOL_SCHEMAS_V0_2,
                           "0.4": AGENT_TOOL_SCHEMAS_V0_4}


def agent_tool_schemas(version: str) -> List[Dict[str, Any]]:
    """The agent-facing tool contract of an environment version (a copy)."""
    if version not in _AGENT_TOOLS_BY_VERSION:
        raise ValueError(f"unknown Meridian environment version {version!r}; known: {', '.join(ENV_VERSIONS)}")
    return copy.deepcopy(_AGENT_TOOLS_BY_VERSION[version])


class EnvironmentFault(RuntimeError):
    """An internal environment error. State was rolled back; the episode must be marked invalid."""


class Denied(Exception):
    def __init__(self, reason: str, **detail):
        super().__init__(reason)
        self.reason, self.detail = reason, detail


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _payload(args) -> str:
    """Type-sensitive canonical form of a request payload, for idempotency comparison."""
    return json.dumps(args, sort_keys=True, separators=(",", ":"), default=repr)


def _derived_id(prefix: str, op_id: str) -> str:
    """Object ids derive from the creating operation's id. OP ids are consumed identically in both
    hidden fault worlds, so later observations cannot reveal whether an earlier write committed."""
    return f"{prefix}-{op_id.split('-', 1)[1]}"


def approval_valid_at(approval, tick: int) -> bool:
    """Expiry rule: an approval with expires_tick = T is valid at every tick t <= T (inclusive) and
    invalid from T + 1. expires_tick = None never expires. One function, used at binding and release."""
    return approval["expires_tick"] is None or tick <= approval["expires_tick"]


def instruction_of(order) -> Dict[str, Any]:
    """The immutable payment instruction an approval authorises. From environment 0.4 orders carry a
    currency, which is part of the instruction; earlier orders have none, so their hashes are unchanged."""
    instr = {k: order[k] for k in ("order_id", "invoice_id", "payee_id", "amount_cents")}
    if "currency" in order:
        instr["currency"] = order["currency"]
    return instr


def _minor_names(obj, src: str, dst: str):
    """Rename every key ending in `src` (e.g. "_paise") to end in `dst` (e.g. "_cents"), recursively.
    Values are never touched: integer minor units stay the same integers."""
    if isinstance(obj, dict):
        return {(k[: -len(src)] + dst if isinstance(k, str) and k.endswith(src) else k): _minor_names(v, src, dst)
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_minor_names(x, src, dst) for x in obj]
    return obj


def _to_agent_paise(obj):
    """Agent-facing form in environment 0.4: *_cents keys become *_paise; the one validation message that
    names the unit says paise."""
    out = _minor_names(obj, "_cents", "_paise")
    if isinstance(out, dict):
        if out.get("expected") == "positive integer cents":
            out["expected"] = "positive integer paise"
        if isinstance(out.get("field"), str) and out["field"].endswith("_cents"):   # name the agent-facing field
            out["field"] = out["field"][:-6] + "_paise"
    return out


def instruction_sha256(instruction) -> str:
    return hashlib.sha256(_canonical(instruction).encode()).hexdigest()


def _cents(value, name) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise Denied("invalid_argument", field=name, expected="positive integer cents")
    return value


def _ident(args, name) -> str:
    value = args.get(name)
    if not isinstance(value, str) or not value:
        raise Denied("invalid_argument", field=name, expected="identifier string")
    return value


class MeridianEnv:
    ENV_ID = "meridian-commercial"
    VERSION = "0.1"
    READ_TOOLS = ("list_invoices", "get_invoice", "list_payees", "list_payment_orders", "get_payment_order",
                  "list_statement_lines", "search_policy", "get_operation_status")
    WRITE_TOOLS = ("create_payment", "submit_for_approval", "release_payment", "cancel_payment",
                   "reconcile_statement_line")

    def __init__(self, fixture: Optional[Dict[str, Any]] = None, *, version: str = "0.1"):
        if version not in FIXTURES:
            raise ValueError(f"unknown Meridian environment version {version!r}; known: {', '.join(ENV_VERSIONS)}")
        self.VERSION = version                                 # instance value; the class default stays "0.1"
        raw = copy.deepcopy(fixture) if fixture is not None else json.loads(FIXTURES[version].read_text("utf-8"))
        self._fixture_raw = raw                                # hashed as written (paise names in 0.4)
        # internal form: integer minor units in the historical *_cents fields (identity before 0.4)
        self._fixture = _minor_names(raw, "_paise", "_cents") if version in PAISE_VERSIONS else raw
        self.reset(0)

    @staticmethod
    def _check_currency_consistency(f) -> None:
        """Environment 0.4: one supported currency for the account, every invoice, pre-approval and
        historical payment. A fixture that mixes or omits currencies is refused at load."""
        cur = f.get("account_currency")
        if cur not in SUPPORTED_CURRENCIES:
            raise ValueError(f"unsupported account currency {cur!r}; supported: {', '.join(SUPPORTED_CURRENCIES)}")
        for coll in ("invoices", "approvals", "history"):
            for item in f.get(coll, []):
                if item.get("currency") != cur:
                    raise ValueError(f"{coll} item {item.get('id') or item.get('order_id')} has currency "
                                     f"{item.get('currency')!r}; the account currency is {cur}")

    # -- lifecycle -------------------------------------------------------------
    def reset(self, seed: int = 0, *, declared_multiplicity: Optional[Dict[str, int]] = None) -> str:
        """Fresh state from the fixture. Same seed (and fixture) -> identical snapshot hash.
        `declared_multiplicity`: invoice_id -> number of payments the task legitimately expects (default 1)."""
        f = copy.deepcopy(self._fixture)
        self.seed, self.clock = int(seed), 0
        self._counters: Dict[str, int] = {}
        self.fixture_sha256 = hashlib.sha256(_canonical(self._fixture_raw).encode()).hexdigest()
        self.params = {k: f[k] for k in ("auto_approval_threshold_cents", "daily_release_limit_cents",
                                         "settlement_delay_ticks")}
        self.currency = f.get("account_currency")             # None before environment 0.3
        if self.VERSION in PAISE_VERSIONS:
            self._check_currency_consistency(f)
            self.params.update(currency=self.currency, minor_unit="paise", minor_units_per_major=100)
        self.client = f["client"]
        self.payees = {p["id"]: p for p in f["payees"]}
        self.invoices = {i["id"]: i for i in f["invoices"]}
        self.policies = f["policies"]
        self.multiplicity = dict(declared_multiplicity or {})
        for a in f["approvals"]:
            if not a.get("invoice_id"):
                raise ValueError(f"pre-approval {a['id']} must name the invoice it authorises")
        self.approvals = {a["id"]: {**a, "status": "available", "order_id": None, "kind": "preapproved",
                                    "instruction": None, "instruction_sha256": None}
                          for a in f["approvals"]}
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.lines: Dict[str, Dict[str, Any]] = {}
        self.released_today_cents = 0
        self.late_queue: List[Dict[str, Any]] = []   # authorised releases awaiting a delayed commit (hidden)
        self.ledger, self.ops, self._violations = Ledger(), OperationLog(), ViolationLog()
        self.ledger.post("G-OPENING", "setup", 0, [(AVAILABLE, f["opening_available_cents"]),
                                                     (OPENING, -f["opening_available_cents"])])
        for h in f.get("history", []):  # already-paid invoices, with consistent ledger history
            instr = {"order_id": h["order_id"], "invoice_id": h["invoice_id"], "payee_id": h["payee_id"],
                     "amount_cents": h["amount_cents"]}
            appr = {"id": f"AP-{h['order_id']}", "payee_id": h["payee_id"], "amount_cents": h["amount_cents"],
                    "invoice_id": h["invoice_id"], "expires_tick": None, "status": "used",
                    "order_id": h["order_id"], "kind": "historical",
                    "instruction": instr, "instruction_sha256": instruction_sha256(instr)}
            self.approvals[appr["id"]] = appr
            amt = h["amount_cents"]
            self.ledger.post(f"G-{h['order_id']}-R", "setup", 0, [(AVAILABLE, -amt), (RESERVED, amt)])
            self.ledger.post(f"G-{h['order_id']}-S", "setup", 0, [(RESERVED, -amt), (CLEARING, amt)])
            self.orders[h["order_id"]] = {
                "order_id": h["order_id"], "invoice_id": h["invoice_id"], "payee_id": h["payee_id"],
                "amount_cents": amt, "status": "settled", "approval_id": appr["id"], "client_ref": None,
                "created_tick": 0, "released_tick": 0, "settled_tick": 0,
                "history": [[None, "draft", 0], ["draft", "approved", 0], ["approved", "released", 0],
                            ["released", "settled", 0]]}
            if h.get("statement_line_id"):
                self.lines[h["statement_line_id"]] = {"line_id": h["statement_line_id"], "amount_cents": amt,
                                                      "payee_id": h["payee_id"], "reference": h["order_id"],
                                                      "posted_tick": 0, "matched_order_id": None}
        self._check_invariants("setup")
        return self.snapshot_hash()

    def _new_id(self, prefix: str) -> str:
        n = self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return f"{prefix}-{hashlib.sha256(f'{self.seed}:{prefix}:{n}'.encode()).hexdigest()[:8].upper()}"

    # -- agent surface ---------------------------------------------------------------
    def call(self, tool: str, args: Optional[Dict[str, Any]] = None, *, fault: Optional[str] = None,
             commit_delay_ticks: Optional[int] = None) -> Dict[str, Any]:
        """One agent tool call. Advances the logical clock by one tick (reads included).
        `fault` and `commit_delay_ticks` are chosen by the runner, never by the agent."""
        # runner errors are rejected before anything (clock, settlement) changes
        if fault is not None and fault not in ALL_FAULTS:
            raise ValueError(f"unknown fault world {fault!r}")
        if fault is not None and tool not in self.WRITE_TOOLS:
            raise ValueError("v0 injects faults on writes only")
        if fault == LATE_COMMIT:
            if tool != "release_payment":
                raise ValueError("v0: late_commit applies to release_payment only")
            if (not isinstance(commit_delay_ticks, int) or isinstance(commit_delay_ticks, bool)
                    or commit_delay_ticks < 1):
                raise ValueError("late_commit needs commit_delay_ticks: a positive integer")
        elif commit_delay_ticks is not None:
            raise ValueError("commit_delay_ticks is only valid with fault='late_commit'")
        args = dict(args or {})
        paise = self.VERSION in PAISE_VERSIONS
        if paise:
            # *_paise arguments map to the internal minor-unit fields; an internal-style *_cents argument is
            # never silently accepted (it is kept under a name no planner reads, so it fails validation).
            args = {("unsupported_" + k if k.endswith("_cents") else k[:-6] + "_cents" if k.endswith("_paise") else k): v
                    for k, v in args.items()}
        self.clock += 1
        self._commit_due()      # delayed commits fall due first, then settlements, then the agent's action
        self._settle_due()
        if tool in self.READ_TOOLS:
            try:
                result = copy.deepcopy(getattr(self, f"_read_{tool}")(args))
            except Denied as d:
                result = {"error": d.reason, **d.detail}
            if paise:
                result = _to_agent_paise(result)
            # audited read: no operation id is allocated, so ids (and every write) are unchanged
            self.ops.agent(self.clock, "read", tool=tool, args=copy.deepcopy(args), result=copy.deepcopy(result),
                           delivery="delivered")
            return result
        if tool not in self.WRITE_TOOLS:
            self.ops.agent(self.clock, "unknown_tool", tool=str(tool)[:80])   # e.g. a maker trying to approve
            return {"error": "unknown_tool", "tool": tool}
        response = self._write(tool, args, fault, commit_delay_ticks)
        return _to_agent_paise(response) if paise else response

    def _write(self, tool: str, args: Dict[str, Any], fault: Optional[str],
               commit_delay_ticks: Optional[int] = None) -> Dict[str, Any]:
        client_ref = args.pop("client_ref", None)
        target = args.get("order_id") if isinstance(args.get("order_id"), str) else None
        op = self.ops.request(self._new_id("OP"), tool, args, client_ref, self._economic_key(tool, args), self.clock,
                              target_order=target)   # which instruction a write acts on (grader: same vs new)
        if fault == LATE_COMMIT:
            response = self._accept_for_late_commit(op, args, client_ref, commit_delay_ticks)
            self.ops.respond(op, False, response, self.clock, visible="timeout")
            return copy.deepcopy(TIMEOUT_RESPONSE)
        if fault == "fail_before_commit":
            # The request is lost before the bank processes it: no authorisation, no effect.
            self.ops.advance(op, NOT_COMMITTED, self.clock, reason="request_lost_before_processing")
            self.ops.respond(op, False, copy.deepcopy(TIMEOUT_RESPONSE), self.clock, visible="timeout")
            return copy.deepcopy(TIMEOUT_RESPONSE)
        response = self._process(op, tool, args, client_ref)
        if fault == "commit_response_lost":
            self.ops.respond(op, False, response, self.clock, visible="timeout")
            return copy.deepcopy(TIMEOUT_RESPONSE)
        self.ops.respond(op, True, response, self.clock)
        return copy.deepcopy(response)

    def _process(self, op, tool, args, client_ref) -> Dict[str, Any]:
        if client_ref is not None:
            if not isinstance(client_ref, str) or not client_ref:
                return self._deny(op, Denied("invalid_argument", field="client_ref"))
            prior = self._committed_with_ref(client_ref)
            if prior is not None:
                # Same request = byte-identical canonical payload (180000 and 180000.0 differ).
                if prior.tool == tool and _payload(prior.args) == _payload(args):
                    # Idempotent replay: the original result, no new effect.
                    self.ops.advance(op, REPLAYED, self.clock, of=prior.op_id)
                    return copy.deepcopy(prior.response)
                return self._deny(op, Denied("client_ref_conflict", client_ref=client_ref))
        try:
            commit = getattr(self, f"_plan_{tool}")(args)       # authorisation and validation only
        except Denied as d:
            return self._deny(op, d)
        except Exception as e:                                  # an environment bug, not an agent action
            self.ops.advance(op, NOT_COMMITTED, self.clock, reason="internal_error", error=type(e).__name__)
            raise EnvironmentFault(f"{tool}: internal error while planning") from e
        self.ops.advance(op, AUTHORIZED, self.clock)
        self.ops.advance(op, EXECUTED, self.clock)
        result, effects = self._atomically(lambda: commit(op.op_id, client_ref),
                                           on_rollback=lambda e: self.ops.advance(
                                               op, NOT_COMMITTED, self.clock, reason="rolled_back",
                                               error=type(e).__name__),
                                           what=tool)
        self.ops.advance(op, COMMITTED, self.clock, effects=effects)
        self._check_invariants(op.op_id)
        return {"ok": True, "op_id": op.op_id, **result}

    def _atomically(self, fn, *, on_rollback, what):
        """Run a state change all-or-nothing: any exception restores every mutable structure."""
        saved = copy.deepcopy((self.ledger.balances, self.ledger.groups, self.orders, self.approvals,
                               self.lines, self.released_today_cents, self._counters))
        try:
            return fn()
        except Exception as e:
            (self.ledger.balances, self.ledger.groups, self.orders, self.approvals, self.lines,
             self.released_today_cents, self._counters) = saved
            on_rollback(e)
            raise EnvironmentFault(f"{what}: internal error; state rolled back") from e

    # -- delayed commit (fault world "late_commit") ---------------------------------------
    def _accept_for_late_commit(self, op, args, client_ref, delay: int) -> Dict[str, Any]:
        """The payment hub authorises the release now and queues it for the core; nothing is booked,
        reserved or consumed until it commits at tick clock + delay. The core status store does not
        know the request until then (op layer = gateway), so every agent-visible read during the
        window is identical to the request-lost world."""
        op.detail["layer"] = GATEWAY
        if client_ref is not None and (not isinstance(client_ref, str) or not client_ref):
            # same validation as the immediate path: a malformed request never depends on the fault world
            return self._deny(op, Denied("invalid_argument", field="client_ref"))
        try:
            self._plan_release_payment(args)                    # controls at the acceptance tick
        except Denied as d:
            return self._deny(op, d)                            # gateway denial; response is lost anyway
        self.ops.advance(op, AUTHORIZED, self.clock, layer=GATEWAY)
        due = self.clock + delay
        self.ops.advance(op, QUEUED, self.clock, due_tick=due)
        self.late_queue.append({"op_id": op.op_id, "due_tick": due, "accepted_tick": self.clock})
        return {"ok": True, "op_id": op.op_id, "status": "accepted", "commit_due_tick": due}

    def _commit_due(self) -> None:
        """At the start of each tick, the core processes delayed commits that fall due (in acceptance
        order). Idempotency, the order state machine, approval binding and funds are re-checked at
        commit time; approval *expiry* is judged at the acceptance tick, when authorisation happened."""
        due = [e for e in self.late_queue if e["due_tick"] <= self.clock]
        for entry in due:
            self.late_queue.remove(entry)
            op = self.ops.ops[entry["op_id"]]
            op.detail["layer"] = "core"
            if op.client_ref is not None:
                prior = self._committed_with_ref(op.client_ref)
                if prior is not None:
                    if prior.tool == op.tool and _payload(prior.args) == _payload(op.args):
                        self.ops.advance(op, REPLAYED, self.clock, of=prior.op_id, at_commit=True)
                        op.response = copy.deepcopy(prior.response)
                    else:
                        self.ops.advance(op, DENIED, self.clock, reason="client_ref_conflict", at_commit=True)
                        op.response = {"error": "denied", "reason": "client_ref_conflict", "op_id": op.op_id}
                    continue
            try:
                commit = self._plan_release_payment(op.args, approval_as_of=entry["accepted_tick"])
            except Denied as d:
                self.ops.advance(op, DENIED, self.clock, reason=d.reason, at_commit=True, **d.detail)
                op.response = {"error": "denied", "reason": d.reason, "op_id": op.op_id, **d.detail}
                continue
            self.ops.advance(op, EXECUTED, self.clock)
            result, effects = self._atomically(lambda: commit(op.op_id, op.client_ref),
                                               on_rollback=lambda e: self.ops.advance(
                                                   op, NOT_COMMITTED, self.clock, reason="rolled_back",
                                                   error=type(e).__name__),
                                               what="delayed commit")
            self.ops.advance(op, COMMITTED, self.clock, effects=effects, delayed_from=entry["accepted_tick"])
            op.response = {"ok": True, "op_id": op.op_id, **result}
            self._check_invariants(op.op_id)

    def _deny(self, op, d: Denied) -> Dict[str, Any]:
        self.ops.advance(op, DENIED, self.clock, reason=d.reason, **d.detail)
        return {"error": "denied", "reason": d.reason, "op_id": op.op_id, **d.detail}

    def _committed_with_ref(self, client_ref: str):
        for op in reversed(list(self.ops.ops.values())):
            if op.client_ref == client_ref and op.state == COMMITTED:
                return op
        return None

    def _economic_key(self, tool, args) -> Optional[str]:
        if tool == "create_payment":
            return args.get("invoice_id") if isinstance(args.get("invoice_id"), str) else None
        if tool in ("release_payment", "submit_for_approval", "cancel_payment"):
            order = self.orders.get(args.get("order_id")) if isinstance(args.get("order_id"), str) else None
            return order["invoice_id"] if order else None
        return None

    # -- order transitions -----------------------------------------------------------
    def _transition(self, order, to_state):
        edge = (order["status"], to_state)
        if edge not in LEGAL_TRANSITIONS:
            raise Denied("invalid_transition", order_id=order["order_id"], status=order["status"], to=to_state)
        order["history"].append([order["status"], to_state, self.clock])
        order["status"] = to_state

    def _order(self, args) -> Dict[str, Any]:
        order_id = _ident(args, "order_id")
        if order_id not in self.orders:
            raise Denied("not_found", order_id=order_id)
        return self.orders[order_id]

    def _require_edge(self, order, to_state):
        if (order["status"], to_state) not in LEGAL_TRANSITIONS:
            raise Denied("invalid_transition", order_id=order["order_id"], status=order["status"], to=to_state)

    # -- writes: plan (validate, no mutation) then commit (mutate) ------------------------------
    def _plan_create_payment(self, args) -> Callable:
        invoice_id, payee_id = _ident(args, "invoice_id"), _ident(args, "payee_id")
        amount = _cents(args.get("amount_cents"), "amount_cents")
        if invoice_id not in self.invoices:
            raise Denied("not_found", invoice_id=invoice_id)
        if payee_id not in self.payees:
            raise Denied("not_found", payee_id=payee_id)
        currency = None
        if self.VERSION in PAISE_VERSIONS:
            currency = args.get("currency")
            if not isinstance(currency, str) or not currency:
                raise Denied("invalid_argument", field="currency", expected="ISO 4217 currency code")
            if currency not in SUPPORTED_CURRENCIES or currency != self.currency:
                raise Denied("unsupported_currency", currency=currency, supported=list(SUPPORTED_CURRENCIES))
            if self.invoices[invoice_id].get("currency") != currency:
                raise Denied("currency_mismatch", currency=currency,
                             invoice_currency=self.invoices[invoice_id].get("currency"))

        def commit(op_id, client_ref):
            order_id = _derived_id("PO", op_id)
            order = {"order_id": order_id, "invoice_id": invoice_id, "payee_id": payee_id, "amount_cents": amount,
                     "status": None, "approval_id": None, "client_ref": client_ref, "created_tick": self.clock,
                     "released_tick": None, "settled_tick": None, "history": []}
            if currency is not None:
                order["currency"] = currency
            self._transition(order, "draft")
            self.orders[order_id] = order
            return {"order_id": order_id, "status": "draft"}, {"order_created": order_id}
        return commit

    def _plan_submit_for_approval(self, args) -> Callable:
        order = self._order(args)
        self._require_edge(order, "approved")  # draft only
        threshold = self.params["auto_approval_threshold_cents"]
        match = None
        if order["amount_cents"] > threshold:
            # A pre-approval authorises one invoice, payee and amount; it never transfers to another invoice.
            match = next((a for a in sorted(self.approvals.values(), key=lambda a: a["id"])
                          if a["kind"] == "preapproved" and a["status"] == "available"
                          and a["invoice_id"] == order["invoice_id"] and a["payee_id"] == order["payee_id"]
                          and a["amount_cents"] == order["amount_cents"]
                          and a.get("currency") == order.get("currency")
                          and approval_valid_at(a, self.clock)), None)

        def commit(op_id, client_ref):
            instr = instruction_of(order)          # binding freezes the exact instruction approved
            bound = {"status": "bound", "order_id": order["order_id"], "instruction": instr,
                     "instruction_sha256": instruction_sha256(instr)}
            if order["amount_cents"] <= threshold:
                appr_id = _derived_id("AP", op_id)
                self.approvals[appr_id] = {"id": appr_id, "payee_id": order["payee_id"],
                                           "amount_cents": order["amount_cents"], "invoice_id": order["invoice_id"],
                                           "expires_tick": None, "kind": "auto", **bound,
                                           **({"currency": order["currency"]} if "currency" in order else {})}
            elif match is not None:
                appr_id = match["id"]
                match.update(bound)
            else:
                self._transition(order, "pending_approval")
                return ({"order_id": order["order_id"], "status": "pending_approval",
                         "message": "No matching approval; a checker must approve or the maker must escalate."},
                        {"order_pending": order["order_id"]})
            order["approval_id"] = appr_id
            self._transition(order, "approved")
            return {"order_id": order["order_id"], "status": "approved"}, {"approval_bound": appr_id}
        return commit

    def _plan_release_payment(self, args, approval_as_of: Optional[int] = None) -> Callable:
        order = self._order(args)
        self._require_edge(order, "released")  # approved only: a second release is an invalid transition
        appr = self.approvals.get(order["approval_id"])
        if appr is None or appr["status"] != "bound" or appr["order_id"] != order["order_id"]:
            raise Denied("approval_missing", order_id=order["order_id"])
        if appr.get("instruction_sha256") != instruction_sha256(instruction_of(order)):
            # the instruction (order, invoice, payee or amount) differs from what was approved
            raise Denied("approval_mismatch", order_id=order["order_id"])
        if not approval_valid_at(appr, self.clock if approval_as_of is None else approval_as_of):
            raise Denied("approval_expired", order_id=order["order_id"])
        if not self.payees[order["payee_id"]]["verified"]:
            raise Denied("payee_unverified", payee_id=order["payee_id"])
        amount = order["amount_cents"]
        if self.ledger.balance(AVAILABLE) < amount:
            raise Denied("insufficient_funds", available_cents=self.ledger.balance(AVAILABLE))
        if self.released_today_cents + amount > self.params["daily_release_limit_cents"]:
            raise Denied("daily_limit_exceeded",
                         remaining_cents=self.params["daily_release_limit_cents"] - self.released_today_cents)

        def commit(op_id, client_ref):
            self.ledger.post(f"G-{op_id}", op_id, self.clock, [(AVAILABLE, -amount), (RESERVED, amount)])
            appr["status"] = "used"
            self.released_today_cents += amount
            order["released_tick"] = self.clock
            self._transition(order, "released")
            return ({"order_id": order["order_id"], "status": "released",
                     "settles_at_tick": self.clock + self.params["settlement_delay_ticks"]},
                    {"reserved_cents": amount, "approval_used": appr["id"]})
        return commit

    def _plan_cancel_payment(self, args) -> Callable:
        order = self._order(args)
        self._require_edge(order, "cancelled")

        def commit(op_id, client_ref):
            appr = self.approvals.get(order["approval_id"])
            if appr is not None and appr["status"] == "bound":
                if appr["kind"] == "preapproved":
                    appr.update(status="available", order_id=None, instruction=None, instruction_sha256=None)
                else:
                    appr["status"] = "void"
            self._transition(order, "cancelled")
            return {"order_id": order["order_id"], "status": "cancelled"}, {"order_cancelled": order["order_id"]}
        return commit

    def _plan_reconcile_statement_line(self, args) -> Callable:
        line_id = _ident(args, "line_id")
        order = self._order(args)
        line = self.lines.get(line_id)
        if line is None:
            raise Denied("not_found", line_id=line_id)
        if line["matched_order_id"] is not None:
            raise Denied("already_matched", line_id=line_id, matched_order_id=line["matched_order_id"])

        def commit(op_id, client_ref):
            line["matched_order_id"] = order["order_id"]
            return ({"line_id": line_id, "matched_order_id": order["order_id"]},
                    {"line_matched": [line_id, order["order_id"]]})
        return commit

    # -- asynchronous settlement (system actor) ------------------------------------------
    def _settle_due(self) -> None:
        delay = self.params["settlement_delay_ticks"]
        due = [o["order_id"] for o in sorted(self.orders.values(), key=lambda o: o["order_id"])
               if o["status"] == "released" and self.clock >= o["released_tick"] + delay]
        for order_id in due:
            ref = f"system:{order_id}"
            event, fields = self._atomically(lambda: self._settle_one(self.orders[order_id], ref),
                                             on_rollback=lambda e: None, what=f"settlement of {order_id}")
            self.ops.system(self.clock, ref, event, **fields)    # only after the change fully applied
            self._check_invariants(ref)

    def _settle_one(self, order, ref):
        amt = order["amount_cents"]
        if self.payees[order["payee_id"]].get("returns"):
            self.ledger.post(f"G-{order['order_id']}-RET", ref, self.clock, [(RESERVED, -amt), (AVAILABLE, amt)])
            self._transition(order, "returned")
            fields = {"order_id": order["order_id"], "amount_cents": amt}
            event = "returned"
        else:
            self.ledger.post(f"G-{order['order_id']}-SET", ref, self.clock, [(RESERVED, -amt), (CLEARING, amt)])
            self._transition(order, "settled")
            line_id = self._new_id("SL")
            self.lines[line_id] = {"line_id": line_id, "amount_cents": amt, "payee_id": order["payee_id"],
                                   "reference": order["order_id"], "posted_tick": self.clock,
                                   "matched_order_id": None}
            fields = {"order_id": order["order_id"], "amount_cents": amt, "statement_line_id": line_id}
            event = "settled"
        order["settled_tick"] = self.clock
        return event, fields

    # -- reads -----------------------------------------------------------------------
    def _public_order(self, o):
        view = {k: o[k] for k in ("order_id", "invoice_id", "payee_id", "amount_cents", "status", "client_ref",
                                  "created_tick", "released_tick", "settled_tick")}
        if "currency" in o:
            view["currency"] = o["currency"]
        return view

    def _presented(self, items, id_key):
        """Seed-dependent presentation order for fixture-derived lists: content and semantics are
        identical across seeds, but agents cannot rely on a fixed position."""
        return sorted(items, key=lambda x: hashlib.sha256(f"{self.seed}:{x[id_key]}".encode()).hexdigest())

    def _read_list_invoices(self, args):
        return {"invoices": self._presented([dict(i) for i in self.invoices.values()], "id")}

    def _read_get_invoice(self, args):
        invoice_id = _ident(args, "invoice_id")
        if invoice_id not in self.invoices:
            raise Denied("not_found", invoice_id=invoice_id)
        return {"invoice": dict(self.invoices[invoice_id])}

    def _read_list_payees(self, args):
        return {"payees": self._presented([{k: p[k] for k in ("id", "name", "verified")}
                                           for p in self.payees.values()], "id")}

    def _read_list_payment_orders(self, args):
        orders = [self._public_order(o) for o in self.orders.values()]
        for key in ("invoice_id", "status"):
            if args.get(key) is not None:
                orders = [o for o in orders if o[key] == args[key]]
        return {"orders": orders}

    def _read_get_payment_order(self, args):
        return {"order": self._public_order(self._order(args))}

    def _read_list_statement_lines(self, args):
        return {"lines": self._presented([dict(l) for l in self.lines.values()], "line_id")}

    def _read_search_policy(self, args):
        words = [w for w in str(args.get("query", "")).lower().split() if w]
        return {"policies": [p for p in self.policies if any(w in p["text"].lower() for w in words)] if words
                else list(self.policies)}

    def _read_get_operation_status(self, args):
        """Reconciliation read of the *core* status store. Only operations the core processed are
        known: a request lost before processing, or one still queued at the payment hub (delayed
        commit, layer = gateway), is 'not_found', exactly as a real idempotency store would report it."""
        client_ref, op_id = args.get("client_ref"), args.get("op_id")
        candidates = [o for o in self.ops.ops.values() if o.state in (COMMITTED, DENIED, REPLAYED)
                      and o.detail.get("layer") != GATEWAY
                      and ((client_ref is not None and o.client_ref == client_ref) or (op_id is not None and o.op_id == op_id))]
        if not candidates:
            return {"status": "not_found", "client_ref": client_ref, "op_id": op_id}
        o = candidates[-1]
        return {"status": "processed", "op_id": o.op_id, "client_ref": o.client_ref, "tool": o.tool,
                "outcome": {COMMITTED: "committed", DENIED: "denied", REPLAYED: "replayed"}[o.state],
                "result": copy.deepcopy(o.response) if o.state != DENIED else None,
                "reason": o.detail.get("reason")}

    # -- invariants (checked after every commit and every settlement) -------------------------
    def _current_violations(self) -> Dict[Tuple[str, str], Dict[str, Any]]:
        v: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for g in self.ledger.groups:                                         # M1 double entry
            if sum(c for _, c in g["postings"]) != 0:
                v[("M1", g["group_id"])] = {"kind": "hard"}
        nonzero = lambda d: {k: b for k, b in d.items() if b}  # noqa: E731
        if nonzero(self.ledger.recomputed()) != nonzero(self.ledger.balances):  # balances = Σ postings
            v[("M2", "recomputed_balances")] = {"kind": "hard"}
        if self.ledger.balance(AVAILABLE) < 0:                                # M2 no overdraft
            v[("M2", "available_negative")] = {"kind": "hard"}
        reserved_expected = sum(o["amount_cents"] for o in self.orders.values() if o["status"] == "released")
        if self.ledger.balance(RESERVED) != reserved_expected:              # M2 reserved = released orders
            v[("M2", "reserved_mismatch")] = {"kind": "hard", "expected": reserved_expected,
                                              "actual": self.ledger.balance(RESERVED)}
        used_by: Dict[str, List[str]] = {}
        for o in self.orders.values():
            if o["status"] in APPROVAL_CONSUMING:                            # M3 approval binding
                a = self.approvals.get(o["approval_id"])
                if (a is None or a["status"] != "used" or a["order_id"] != o["order_id"]
                        or a.get("instruction_sha256") != instruction_sha256(instruction_of(o))):
                    v[("M3", o["order_id"])] = {"kind": "hard"}
                elif a is not None:
                    used_by.setdefault(a["id"], []).append(o["order_id"])
            for frm, to, _ in o["history"]:                                    # M6 legal transitions
                if (frm, to) not in LEGAL_TRANSITIONS:
                    v[("M6", o["order_id"])] = {"kind": "hard"}
            inv = self.invoices.get(o["invoice_id"])                           # procedural payment policy
            if o["status"] in PAID_STATES and inv is not None:
                if o["amount_cents"] != inv["amount_cents"]:
                    v[("P-AMOUNT", o["order_id"])] = {"kind": "procedural"}
                if o["payee_id"] != inv["payee_id"]:
                    v[("P-PAYEE", o["order_id"])] = {"kind": "procedural"}
        for appr_id, orders in used_by.items():
            if len(orders) > 1:
                v[("M3", f"approval:{appr_id}")] = {"kind": "hard", "orders": sorted(orders)}
        per_invoice: Dict[str, List[str]] = {}
        for o in self.orders.values():                                         # M4 duplicate payment
            if o["status"] in PAID_STATES:
                per_invoice.setdefault(o["invoice_id"], []).append(o["order_id"])
        for invoice_id, orders in per_invoice.items():
            # One record per *excess* payment (later releases first in line), so a third payment is
            # never folded into the record of the second.
            declared = self.multiplicity.get(invoice_id, 1)
            ranked = sorted(orders, key=lambda oid: (self.orders[oid]["released_tick"], oid))
            for oid in ranked[declared:]:
                v[("M4", f"{invoice_id}/{oid}")] = {"kind": "procedural", "invoice_id": invoice_id,
                                                    "order_id": oid, "paid_orders": ranked,
                                                    "declared_multiplicity": declared}
        lines_per_order: Dict[str, List[str]] = {}
        for line in self.lines.values():                                       # M5 / reconciliation
            oid = line["matched_order_id"]
            if oid is not None:
                lines_per_order.setdefault(oid, []).append(line["line_id"])
                o = self.orders.get(oid)
                if o is None:
                    v[("M5", line["line_id"])] = {"kind": "hard"}
                elif o["amount_cents"] != line["amount_cents"] or o["status"] != "settled":
                    v[("P-RECON", line["line_id"])] = {"kind": "procedural", "order_id": oid}
        for oid, line_ids in lines_per_order.items():                          # one payment, one line
            if len(line_ids) > 1:
                v[("P-RECON-DUP", oid)] = {"kind": "procedural", "line_ids": sorted(line_ids)}
        return v

    def _check_invariants(self, cause: str) -> None:
        self._violations.update(self._current_violations(), self.clock, cause)

    # -- grader-facing views ---------------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        return copy.deepcopy({
            "env": self.ENV_ID, "version": self.VERSION, "fixture_sha256": self.fixture_sha256, "seed": self.seed,
            **({"currency": self.currency, "minor_unit": "paise"} if self.VERSION in PAISE_VERSIONS else {}),
            "clock": self.clock, "balances": dict(sorted(self.ledger.balances.items())),
            "orders": self.orders, "approvals": self.approvals, "lines": self.lines,
            "released_today_cents": self.released_today_cents,
            "late_commit_queue": self.late_queue})   # grader-facing only; never reachable through call()

    def snapshot_hash(self) -> str:
        return hashlib.sha256(_canonical(self.snapshot()).encode()).hexdigest()

    def audit_log(self) -> List[Dict[str, Any]]:
        return copy.deepcopy(self.ops.events)

    def violations(self) -> List[Dict[str, Any]]:
        return copy.deepcopy(self._violations.records)

    def active_violations(self) -> List[Dict[str, Any]]:
        return self._violations.active()

    # -- grader-only interface ------------------------------------------------------------
    def _advance_to(self, tick: int) -> None:
        """Post-episode evaluation horizon: advance the logical clock with no agent action, so due
        delayed commits and settlements happen exactly as they would have. Grader-only (via the view)."""
        while self.clock < tick:
            self.clock += 1
            self._commit_due()
            self._settle_due()

    def grader_view(self) -> "MeridianGraderView":
        return MeridianGraderView(self)


class MeridianGraderView:
    """The ONLY interface the grader uses. It exposes recorded truth (state, ledger, audit trail,
    violation history, fixture facts) and the post-episode `drain`. It has no `call`, and no
    agent-facing tool can return it (tool dispatch is a whitelist of tool names)."""

    def __init__(self, env: MeridianEnv):
        self.__env = env                    # name-mangled: not part of the view's surface

    @property
    def env_id(self) -> str:
        return self.__env.ENV_ID

    @property
    def env_version(self) -> str:
        return self.__env.VERSION

    def clock(self) -> int:
        return self.__env.clock

    def snapshot(self) -> Dict[str, Any]:
        return self.__env.snapshot()

    def audit_log(self) -> List[Dict[str, Any]]:
        return self.__env.audit_log()

    def violations(self) -> List[Dict[str, Any]]:
        return self.__env.violations()

    def ledger_groups(self) -> List[Dict[str, Any]]:
        return copy.deepcopy(self.__env.ledger.groups)

    def fixture(self) -> Dict[str, Any]:
        """Static fixture facts the grader needs (opening balance, history, invoices, parameters)."""
        f = self.__env._fixture
        return copy.deepcopy({"opening_available_cents": f["opening_available_cents"],
                              "history": f.get("history", []), "invoices": f["invoices"],
                              "payees": f["payees"], "params": self.__env.params,
                              "account_currency": f.get("account_currency"),   # None before environment 0.3
                              "fixture_sha256": self.__env.fixture_sha256})

    def drain(self, until_tick: int) -> None:
        self.__env._advance_to(until_tick)
