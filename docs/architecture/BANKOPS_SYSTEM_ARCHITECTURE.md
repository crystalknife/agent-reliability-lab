# BankOps system architecture

**Revised 2026-11-17 (Day 2).** Base commit `ae6ea37`; branch `feat/bankops-observatory`.

**Labels:** VERIFIED (inspected in this worktree) / INFERRED / DECISION.

## 1. Repository audit (VERIFIED unless marked)
| Area | What exists | Gap for BankOps |
|---|---|---|
| Environment | `src/minilab/env.py:10` `MiniBankEnv`: "In-memory copy of bank.json. Never mutates the file." Methods `get_customer` (:21), `get_account` (:27), `get_transactions` (:41), `search_policy` (:47), `snapshot` (:18) | **Read-only.** No writes, approvals, pending states, idempotency, audit log, invariants, permissions, fault worlds or reset API |
| Fixture | `data/bank.json`: 3 customers, 4 accounts, 5 transactions, 1 policy | Too small; no ledger |
| Tools | `src/minilab/tools.py`: `get_customer`, `get_account`, `get_transactions`, `search_policy`, `calculate`. `dispatch` at :85 | No registry; every tool is always exposed |
| Agent loop | `src/minilab/agent.py:113` `run_agent`. Contract at :117: `model_fn(messages, tool_schemas, config) -> {"tool","arguments"} \| {"calls",[...]} \| {"final"}`. It always passes the global `TOOL_SCHEMAS` (:145) | No per-run allow-list |
| Model adapters | `src/minilab/models/`: `openai`, `local`, `omniroute`, `openrouter`, `groq` (all **openai SDK** with a `base_url`) and `gemini` (google SDK). `local.py` already targets an **OpenAI-compatible `/v1`** server at the fixed `LOCAL_BASE_URL = "http://127.0.0.1:8081/v1"` | **Corrects Day 1:** a local OpenAI-compatible path exists, but its URL is fixed. **No explicit timeouts** (grep found none), no typed errors, no health check; API keys are read straight from the environment |
| Fault attribution | `failures.fault_origin`; provider status trusted only for `_SDK_ROOTS` (:174) | Typed errors raised by a new adapter would be classified `model_call_local` (INFERRED). BankOps needs its own attribution field |
| Tasks and evaluation | `tasks.py` (10 tasks, `TASK_SCHEMA_VERSION = "1"`); `evaluation.py:177` grades answer text | No state grading. **Frozen as the legacy suite** |
| Provenance and statistics | `run_manifest.py` (self-hashed, includes `experiment_id`); `stats.py` (Wilson, Newcombe); `workflow.py`; `legacy_anchors.py` | Manifest lacks environment, tool-set, fault and grader identity; no Kish/GEE or hierarchical-bootstrap tools |
| Live service | `live/server.py` (loopback HTTP/SSE, hardened in PR #14); `store.py`; `recorder.py`; `sanitize.py`; `synthetic.py` | No endpoint for creating real runs (by design so far) |
| Frontend | Routes in `frontend/src/App.tsx:34-43`; `frontend/src/layouts/LabShell.tsx` | No BankOps pages |
| EXP-005 modules | `task_schema.py`, `suite_design.py` exist **only untracked in the original worktree**, not on this branch | Decision D9 |

## 2. Reuse map
| Component | Decision |
|---|---|
| `MiniBankEnv`, the 10 tasks, `evaluate()`, `FAILURES`, `fault_origin` | **Frozen.** Never changed for BankOps |
| `agent.run_agent` | Used unchanged by legacy runs. BankOps calls it with the registry's tool set and dispatcher, or with a thin wrapper loop. Legacy behaviour stays pinned by the existing tests |
| `models/local.py` conversion logic | Mirrored by the new adapter (same message and tool translation; same return shape) |
| `run_manifest.py` | Extended additively (dataset strategy §3) |
| `live/store.py`, `recorder.py`, `sanitize.py`, `server.py` | Reused; BankOps adds read-only routes |
| Frontend `display` and `anim` components | Reused |

## 3. Target module layout (DECISION D5: `src/minilab/bankops/`)
```
bankops/
  models.py          ModelAdapter protocol; OpenAICompatibleAdapter (stdlib HTTP, decision D4); ModelProfile;
                     env:NAME credential refs; typed errors (auth, not_found, rate_limited, server, timeout,
                     transport, malformed, config) carrying status and origin; health_check()       [Day 3]
  ledger.py          Ledger (zero-sum posting groups), OperationLog (lifecycle + audit events),
                     ViolationLog (open/close records)                                           [BUILT]
  envs/meridian.py   commercial payments: maker-checker, approvals, async settlement, reconciliation;
                     reset(seed), call(tool, args, fault=), snapshot(), audit_log(), violations() [BUILT]
  envs/base.py       shared protocol and capability token: only if Harbor needs it (Day 5)
  envs/harbor.py     retail servicing: synchronous transfers, fee refunds                        [Day 5]
  faults.py          fault worlds: none, fail_before_commit, commit_response_lost (late_settlement: wk 2)
  tools/registry.py  capability registry -> (schemas, dispatcher) from one allow-list; P1–P6 tests [Day 5]
  suites/registry.py suite and task versions; compatibility check (never auto-enables)          [Day 5]
  grading.py         readiness §5.4 checks; never trusts the report for state                    [Day 6]
  run.py             repeats × worlds × seeds -> episode rows + manifest + live events          [Day 6]
```

## 4. Runtime data flow
```
Observatory (loopback) ──GET──> live.server read-only BankOps routes            (week 1)
CLI / runner ──> bankops.run(config) ──validate──> suites.compat(env, tools, tasks)
   for task × world × repeat:
     env = envs[id].reset(seed); toolset = tools.build(enabled, env)  # schemas + dispatcher
     run_agent(adapter.as_model_fn(), toolset, max_steps) -> trajectory
     env.audit_log() + env.snapshot() + finish report -> grading.grade() -> episode row
   -> runs/<run_id>/{episodes.jsonl, manifest.json}  (git-ignored; never under results/)
```

## 5. Security and integrity invariants
1. **Credentials:** only `env:NAME` references, resolved when a request is sent. Values never appear in profiles, reprs, errors, logs, manifests or live events (test A9).
2. **Loopback only:** the server stays loopback-only and unauthenticated by design. BankOps adds **read-only** routes in week 1, and run creation stays CLI-only.
3. **Disabled tools** are absent from schemas **and** rejected at dispatch; environment writes require a capability token (P1–P6).
4. **Isolation:** each episode gets a fresh environment via `reset(seed)`. No shared state.
5. **Legacy:** legacy modules, historical `results/` and published exports are read-only inputs.
6. **External agent code** never runs in-process (`BANKOPS_PUBLIC_API_AND_SUBMISSIONS.md`).

## 6. Open architecture decisions
D4 (stdlib adapter), D5 (location and `runs/`), D3 (`finish` contract), D9 (EXP-005 modules). See readiness §10.
