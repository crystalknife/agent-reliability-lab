# BankOps long-horizon reliability methodology (METR-inspired)

**Revised 2026-11-17 (Day 2).** This is option **B** in `docs/roadmap/BANKOPS_DAY2_READINESS.md` (decision D1).
- **Status:** a protocol only.
- **No time-horizon claim** is permitted until the human-baseline protocol in §3 has been carried out.

**Labels:** VERIFIED / INFERRED / HYPOTHESIS / UNKNOWN.

## 1. What METR does (VERIFIED: [2503.14499](https://arxiv.org/abs/2503.14499), full text)
- **Task time:** "the geometric mean time of successful human baselines". Failed baselines are excluded.
- **Volume:** "over 800 baselines totaling 2,529 hours, of which 558 baselines (286 successful) come from HCAST and RE-Bench, and 249 (236 successful) from" SWAA.
- **Model:** p(success) = σ((log h − log t)·β) per agent; h is the 50% horizon. "models' 80% time horizons are 4-6x shorter".
- **Runs:** "We perform 8 runs" per agent and task.
- **Weighting:** each task gets "the inverse square root of the number of tasks in the family".
- **Intervals:** 95% CI by "hierarchical bootstrap over task families, tasks, and task attempts".

## 2. Adapting it to banking
### Difficulty axes are recorded separately and never merged into one score
| Axis | Definition | Source |
|---|---|---|
| Human time t | Geometric mean of successful qualified baselines (§3) | **Measured only. Never estimated or invented** |
| Operation count | State-changing operations in the reference solution | Reference solution |
| Dependency depth | Longest dependency chain among operations | Task graph (`src/minilab/workflow.py` exists on this branch; schema-2 tasks need the EXP-005 `task_schema.py`, decision D9) |
| Width and branching | Independent branches; policy-conditional decision points | Task graph |
| Evidence items | Distinct facts that must be read and used | Task specification |
| Policy complexity | Clauses applied | Task specification |
| State transitions | Ledger and object transitions, including asynchronous ones | Grader |
| Interruptions | Injected fault worlds | Fault plan |

**Operation or tool-call counts are not time and must never be called a "time horizon".**

### H1 design (exploratory)
- **Pairing:** matched variants that use the **same operations**, either *chained* (each amount derived from the previous result) or *independent* (amounts given). This varies depth with operation count held fixed.
- **Remaining confounds, to report:** prompt length and evidence count.
- **Related work:** ToolMaze ([2606.05806](https://arxiv.org/abs/2606.05806)) already varies DAG complexity in generic tools (VERIFIED). The banking value comes from the human-time dimension.

## 3. Human completion-time baseline protocol (required before any horizon claim)
1. **Baseliners:**
   - People with **at least 2 years** of banking-operations or payments-operations experience. Role and tenure are recorded; identities are pseudonymised.
   - Paid, with informed consent (decision D7).
   - **At least 3 different baseliners per task**, so no task's time rests on one person.
2. **Same conditions as the agents:**
   - the same environment, fixture and tools, through a minimal human UI;
   - the same written task;
   - no outside help.
   - **Timer:** starts on first view of the task and stops on `finish`.
3. **Same grader:** human success is decided by the **same deterministic grader** as agent success (`BANKOPS_DAY2_READINESS.md` §5.4).
4. **Aggregation:**
   - t = geometric mean over **successful** baselines (METR).
   - **Also reported** (unlike METR): the failure count per task, and the horizon recomputed **including** failed attempts (time-to-give-up) as a sensitivity analysis, because filtering on success biases t downward (METR acknowledges this).
5. **Uncertainty of t:** a bootstrap over baselines within a task, propagated into the horizon fit by resampling the t values within each outer bootstrap replicate.
6. **Pilot first:** 3 tasks × 3 baseliners, to measure the variance of time and the success rate before scaling (week 3 or later).

## 4. Model trials and statistics
| Item | Specification |
|---|---|
| Trials | k = 8 per (model, task, condition); fixed seed schedule |
| Units | **Observation** = episode. **Clusters** = task within family. Inference resamples families and tasks, not just episodes |
| Per-task rates | Descriptive Wilson intervals. At n = 8 and k = 4, the 95% Wilson interval is **[0.215, 0.785], half-width ±0.285** (computed; the Day 1 figure of ±0.31 was wrong) |
| Horizon fit | Per-model logistic fit of success on log₂ t. h₅₀ and h₈₀ by hierarchical bootstrap (families → tasks → episodes), with tasks weighted 1/√(family size) as in METR |
| Few-cluster caution | With fewer than about 15 families, a cluster bootstrap is anti-conservative (Limbo makes the same point, VERIFIED). Also report Wilson intervals on a Kish effective sample size, n/(1+(m̄−1)ρ) |
| pass^k | Reported for consistency (τ-bench, Bank-World) |
| Invalid episodes | Provider, environment and evaluator errors are excluded from denominators and reported separately |
| Multiplicity | One pre-registered primary contrast per study; Holm correction otherwise |
| Sensitivity | Leave one family out; with and without fault worlds; with and without failed baselines |

## 5. Claim levels
- **Descriptive:** rates and curves, with intervals.
- **Exploratory:** axis effects.
- **Confirmatory:** only pre-registered contrasts on a frozen suite, with human baselines collected.
