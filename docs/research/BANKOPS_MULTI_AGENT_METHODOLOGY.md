# BankOps multi-agent methodology (Silo-Bench-inspired), EXP-008

**Revised 2026-11-17 (Day 2). Status: later research phase, design only.**

**Prerequisites:**
- a frozen single-agent baseline on the same tasks;
- a validated deterministic grader (`docs/roadmap/BANKOPS_DAY2_READINESS.md` §5.5).

No multi-agent run or claim comes before these. Multiple agents are not added to make the benchmark look advanced.

## 1. Source facts (VERIFIED: [2603.01045](https://arxiv.org/abs/2603.01045), full text; code under the Unlicense)
- "30 algorithmic tasks across three communication complexity levels, evaluating 54 configurations over 1,620 experiments".
- Team sizes N ∈ {2, 5, 10, 20, 50, 100}.
- **Coordination cost:** "Relative Coordination Cost (RCC) = 1−SR(N=k)/SR(N=1)".
- **Finding:** a "Communication-Reasoning Gap". Agents exchange information but fail to synthesise it.
- **Budgets:** the paper uses a per-round *action budget*. Matched communication budgets are **not** a feature of Silo-Bench (INFERRED from the text). BankOps would add them.

## 2. Banking adaptation (HYPOTHESIS design)
- **Evidence partitioning by role:**
  - customer profile and KYC;
  - ledger history;
  - policy documents;
  - approvals and case notes.
  - **Only one agent holds write tools.** This separates coordination failures from concurrent-write conflicts. A later variant relaxes this to study duplicate side effects across agents.

| Code | Condition |
|---|---|
| SA-full | One agent with all evidence and tools (the N = 1 reference) |
| MA-hub | N agents plus an orchestrator |
| MA-p2p | N agents with addressed messages |
| MA-store | N agents with a shared notes store |

- **Team size:** N ∈ {2, 3, 4}. Banking tasks do not shard naturally to large N.
- **Matched budgets:** equal **total** tokens, tool calls and wall-clock across conditions. Results are also reported with no budget cap.
- **Factors:** information completeness (all facts / one missing / one stale or conflicting) × workflow depth (from the long-horizon axes).

## 3. Metrics
| Metric | Definition | Deterministic? |
|---|---|---|
| Task success; state correctness | The same grader as single-agent runs | Yes |
| Evidence reconstruction accuracy | Required structured facts that appear correctly in the decision record | Yes for structured facts; otherwise annotated |
| Communication volume | Messages; tokens in messages | Yes |
| Cost | Tokens and API cost per success | Yes |
| RCC | 1 − SR(MA)/SR(SA-full) at matched budget | Yes |
| Integration errors | Facts received but unused or misused | Human (two annotators, κ) |
| Propagated false assumptions | A false claim from one agent adopted by another | Human |
| Side-effect safety | Duplicate or unauthorised effects | Yes |

## 4. Design and statistics
- **Within-task design:** every task runs under every condition, giving paired contrasts.
- **Tests:** McNemar or GEE clustered on task (as in the Harness paper, VERIFIED), with a hierarchical or Kish-adjusted interval.
- **One pre-registered primary contrast:** SA-full versus MA-hub at matched budget. Topology contrasts are exploratory.

## 5. Open questions
- **OPEN-M1:** heterogeneous models across roles?
- **OPEN-M2:** which scaffold, and how to keep scaffolds comparable? The Harness paper shows the harness alone shifts compliance by up to 11.5 points (VERIFIED: Cochran's Q).
- **OPEN-M3:** can the evidence-reconstruction ground truth be fully structured?
