# BankOps implementation backlog (re-prioritised)

**Revised 2026-11-17 (Day 2).** P0 = week-1 slice (readiness §7); P1 = week 2; P2 = later. **D1 was decided (option A, 2026-11-17); implementation status is tracked in `BANKOPS_DAY2_READINESS.md` §5.** Every item ships with tests, and legacy behaviour stays byte-identical.

**Labels:**
- Every item is a **DECISION**: proposed and unapproved.
- File paths of existing modules (`stats.py`, `run_manifest.py`, `live/server.py`, `frameworks/langchain.py:164`): **VERIFIED** in this worktree.
- Effort and priority: **INFERRED** estimates.

| ID | Pri | Item | Files | Acceptance | Depends on |
|---|---|---|---|---|---|
| B-00 | P0 | Owner decisions D1–D5 (readiness §10) | docs | Recorded in readiness §10 | — |
| B-01 | P0 | ModelAdapter protocol, OpenAI-compatible stdlib adapter, profiles (API and local), `env:` credential refs, typed errors, timeouts, health check | `src/minilab/bankops/models.py`, `tests/test_bankops_models.py` | A1–A10 | D1, D4, D5 |
| B-02 | P0 | Ledger and 8-stage event model | `bankops/ledger.py`, `events.py` | Invariant unit tests; atomic commit | B-00 |
| B-03 | P0 | Environment protocol, registry, reset(seed), snapshot hash, capability token | `bankops/envs/base.py` | Determinism and isolation tests; P5 | B-02 |
| B-04 | P0 | Meridian v0 (create, submit, release, cancel; approvals; settlement clock) | `envs/meridian.py`, fixture | M1–M6 hold; denials correct | B-03, D2 |
| B-05 | P0 | Fault worlds `fail_before_commit` and `commit_response_lost` | `bankops/faults.py` | Byte-identical agent observation across the pair; events correct | B-04 |
| B-06 | P0 | Harbor v0 (transfer, refund) | `envs/harbor.py`, fixture | I1–I6 hold | B-03, D2 |
| B-07 | P0 | Tool registry and runtime enforcement | `bankops/tools/registry.py` | P1–P6 | B-03 |
| B-08 | P0 | Suite registry, compatibility check, 8 tasks (F1, F2 × depth 1–2) with oracles | `bankops/suites/` | Oracle passes; incompatible configurations rejected | B-04, B-06, B-07 |
| B-09 | P0 | Grader (readiness §5.4) and `finish` contract | `bankops/grading.py` | Unit tests per check | B-05, B-08, D3 |
| B-10 | P0 | **Grader validation** (scripted faulty agents, trace mutations, observation equivalence) | `tests/test_bankops_grader_validation.py` | Each faulty agent caught by its intended check | B-09 |
| B-11 | P0 | Runner (repeats × worlds × seeds), manifest extension, `runs/` output | `bankops/run.py`, `run_manifest.py` (additive) | Identical rows for the scripted oracle over 3 runs | B-09 |
| B-12 | P0 | Read-only results routes; minimal run view; comparable-configuration table | `live/server.py` (new GET routes), `frontend/src/bankops/*` | E2E; cross-class merge rejected | B-11 |
| B-13 | P1 | `late_settlement` and stale-read worlds | `faults.py` | Deterministic per seed | B-05 |
| B-14 | P1 | Families F3 (refund) and F4 (invoice batch); depth 3 variants | suites | Oracles pass | B-08 |
| B-15 | P1 | Statistics: Kish effective-n Wilson, GEE check, pass^k, hierarchical bootstrap | `stats.py` (additive) | Simulation coverage test | B-11 |
| B-16 | P1 | Taxonomy v2 producer and annotation export | `bankops/taxonomy.py` | Legacy `FAILURES` unchanged; mapping tests | B-09 |
| B-17 | P1 | Workbench configuration UI; registry views | frontend | Vitest and E2E | B-12 |
| B-18 | P1 | Pre-registration document for the H6 pilot to confirmatory study | docs | Owner sign-off | B-10 |
| B-19 | P2 | Human-baseline feasibility pilot (3 tasks × 3 experts) | protocol, UI | Executed; no invented times | D7, B-08 |
| B-20 | P2 | Public leaderboard | frontend, API | Admission rules enforced | B-15 |
| B-21 | P2 | Hosted API, sandboxed external submissions | separate service | Security review (API doc §4) | B-20 |
| B-22 | P2 | Multi-agent harness (EXP-008) | — | After the single-agent baseline | B-15 |
| B-23 | — | *ARL, not BankOps:* `frameworks/langchain.py:164` deprecated assignment (fails under `-W error::DeprecationWarning`) | — | Tracked for ARL; out of BankOps scope | — |

**Not in the backlog:** rewriting the existing dashboard, changing historical scores, republishing historical trajectories.
