# BankOps: one-week execution plan (re-baselined)

**Revised 2026-11-17 (Day 2).** This replaces the Day 1 plan. The frozen scope and acceptance tests are in `BANKOPS_DAY2_READINESS.md` §7–§9, and **D1 was decided (option A, 2026-11-17)**; implementation status is tracked in readiness §5.

**Labels:**
- "Done" items: **VERIFIED** (the files exist in this worktree).
- Days 3–7 and the week-1 status column: **DECISION**, a plan pending D1.
- Feasibility statements: **INFERRED** estimates, not commitments.

## 1. What changed and why
- **Research question:** Day 2 had to go to research validation. The Day 1 core claims (H3/H4) turned out to be already established (`docs/research/BANKOPS_RESEARCH_GAP.md` §1).
- **Feasibility:** the Day 1 seven-day MVP (two environments, a configuration UI, a public leaderboard and adapters) is **not achievable at the required rigour** in the remaining five days. In particular, grader validation (readiness §5.5) cannot be skipped. The scope is therefore reduced to one vertical slice.
- **Contradiction resolved:** Day 1 scheduled a fault plan on Day 4 while the backlog marked faults as P1. Now the two matched ambiguity worlds are P0 (they are core to H6), and the other fault worlds are week 2.

## 2. Plan (after D1 is decided)
| Day | Deliverable | Exit criterion |
|---|---|---|
| 2 (today) | Research validation; decision document | **Done** (documents); D1 pending |
| 3 | ModelAdapter slice | Readiness tests A1–A10 green; full Python suite green |
| 4 | Ledger, events, Meridian v0, two fault worlds | Determinism, invariants, observation equivalence, 8-stage events |
| 5 | Harbor v0, tool registry (P1–P6), suite registry, 8 tasks with oracles | P1–P6 green; compatibility rejects; oracle passes |
| 6 | Grader, grader validation, runner, `finish` contract | Scripted faulty agents caught; oracle rows identical over 3 runs |
| 7 | Read-only results routes, minimal run view, comparable-configuration table, scripted pilot (+1 local model if D6) | E2E run → results → class row; cross-class merge rejected by a test |

## 3. The original acceptance criteria: honest status for week 1
| # | Criterion | Week 1 | Note |
|---|---|---|---|
| 1 | User selects a supported model | **Partial** | Model profile chosen via CLI configuration; UI selection in week 2 |
| 2 | One of at least two environments | **Yes** | Harbor v0 and Meridian v0 |
| 3 | Configure enabled tools | **Yes (CLI)**; UI toggles week 2 | Runtime enforcement P1–P6 in week 1 |
| 4 | Compatible suite | **Yes** | Compatibility check |
| 5 | Backend validates configuration | **Yes** | `validate(config)` |
| 6 | Agent executes tasks | **Yes** | Scripted agents; one local model if D6 is approved |
| 7 | Environment records state transitions | **Yes** | 8-stage events, ledger |
| 8 | Evaluator checks final state and invariants | **Yes** | Grader plus grader validation |
| 9 | Repeated trials are reproducible | **Yes (scripted)**; model runs are reproducible in configuration, not in output | Manifest hashes |
| 10 | Observatory shows execution, failures and metrics | **Partial** | Minimal run view; failure analysis week 2 |
| 11 | Validated result appears in a leaderboard | **Partial** | Comparable-configuration table only; public leaderboard week 2 |

Anything not met is reported as incomplete, never stubbed.

## 4. Risks
- **D1 not decided:** the slice stays on hold.
- **Task authoring time:** 8 tasks is the cap.
- **Grader bugs:** mitigated by validation before any model result.
- **Local-model availability on this machine:** D6.
