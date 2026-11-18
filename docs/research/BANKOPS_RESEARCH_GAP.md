# BankOps research gap: validated assessment

**Revised 2026-11-17 (Day 2).** Evidence is in `BANKING_BENCHMARK_LANDSCAPE.md`. Labels: VERIFIED, INFERRED, HYPOTHESIS, UNKNOWN.

## 1. Corrections to Day 1
| Day 1 statement | Status | Evidence |
|---|---|---|
| G1 (the "strongest gap"): agent-facing lost responses, idempotency and duplicates as a controlled factor are uncovered | **Wrong (VERIFIED).** Already studied outside banking | **Limbo** ([2609.29095](https://arxiv.org/abs/2609.29095)): matched "not executed" versus "executed, response lost" worlds behind the same timeout; 12 fault modes; graded against a ledger of committed effects; 25,930 episodes. **"Did It Happen?"**: 81 templates of paired hidden commit states. **IdempotencyBench**: 320 tasks |
| H3 (lost responses expose duplicates that answer-based evaluation misses) is our finding | **Already shown (VERIFIED)** | Limbo: "agents reported success in 90% of the episodes in which they had duplicated an effect" |
| H4 (reconciliation or idempotency affordances reduce duplicates) is open | **Already shown (VERIFIED)** | "Did It Happen?": prompt advice 0.4979, status reconciliation 0.8066, stable idempotency key 1.0000. Limbo: idempotency keys lower the duplicate rate "from 28% to 4%" |
| H2 (transient faults and stale data degrade agents) is open | **Already shown in general tool use (VERIFIED)** | ReliabilityBench, ToolMaze, AgentCheck ("stale-data faults remain near 3-4 of 10"), Loud/Quiet |
| BizAgentBench "resumes interruptions" | **Correct, and more precise now (VERIFIED)** | It *removes* transport faults from the agent's experience and tracks them separately |
| Bank-World observed duplicate fee charging | **Correct, with context (VERIFIED)** | Arithmetic or procedure errors ("charging the fees twice"), not responses to ambiguity |
| Licences of BizAgentBench, TELLER and others | **Several corrected (VERIFIED)** | Landscape §5 |

## 2. What remains unproven (gaps supported by the verified evidence)
- **U1, banking controls during recovery:**
  - Prior fault and ambiguity work (Limbo, "Did It Happen?") has **no authorisation controls**. A keyword search for "approv|authori" finds only related-work or author-contribution sentences (V-absent in the task design).
  - Banking benchmarks *with* controls (Bank-World, BizAgentBench, Northbridge) **inject no agent-facing faults** (V-absent; BizAgentBench removes them by design).
  - **Open question (HYPOTHESIS):** when an operation's outcome is uncertain, does recovery break banking controls or ledger integrity? Examples:
    - a second payment *order* for the same invoice, which an idempotency key on the first order cannot catch;
    - reusing or re-seeking an approval;
    - an unauthorised reversal used as compensation;
    - reporting a *pending* settlement as *settled*.
- **U2, human-baselined banking reliability curves:**
  - No retrieved banking-operations benchmark reports expert completion times (INFERRED: Bank-World, BizAgentBench and TELLER do not state any). BankerToolBench reports them, but for investment-banking deliverables, not ledger operations (VERIFIED).
  - ToolMaze varies DAG complexity, but not in banking and without human times (VERIFIED).
- **U3, multi-agent evidence partitioning in banking:** Silo-Bench uses algorithmic tasks (VERIFIED). Nothing banking-specific was retrieved.

## 3. Hypotheses: assessment
| H | Verdict | Reason |
|---|---|---|
| H1 (depth reduces success with operation count held fixed) | **Retain as exploratory, secondary** | Confounds: evidence count, prompt length and tool count grow with depth. Design: matched pairs that use the **same operations** chained versus independent. Partly covered generically (ToolMaze). Banking value comes mainly with human times (U2) |
| H2 (transient or stale faults reduce success and raise violations) | **Reject as a contribution**; keep as a manipulation check | Already established generically. Its only use is as a reference that the faults bite in our environments |
| H3 (lost responses expose duplicates that answer grading misses) | **Reject as novel**; keep as a **pre-registered replication** in banking | Limbo has shown it. A banking replication tests external validity and is labelled as such |
| H4 (reconciliation affordances reduce duplicates) | **Reject as novel**; keep as **replication plus extension** (see H6) | Shown in "Did It Happen?" and Limbo |
| H5 (configuration differences grow under uncertainty) | **Exploratory only** | Limbo already decomposes variance (model 53% when an immediate read-back resolves the fault; contract 81% when it cannot). An interaction test needs power we will not have in week 1 |
| **H6 (new, HYPOTHESIS): recovery constrained by controls** | **Candidate primary** (if decision D1 picks option A in the readiness document) | Defined in §4 |

## 4. H6, specified (candidate primary)
**Statement.** With banking controls in place, ambiguous outcomes on control-gated operations produce ledger-integrity breaches or control violations during recovery that a status-read affordance alone does not prevent. An idempotency key on the payment *instruction* prevents same-instruction duplicates, but not cross-instruction duplicates or misreported settlement.

| Element | Specification |
|---|---|
| Independent variables | **Fault world**, within task and matched. **Implemented:** none / `fail_before_commit` / `commit_response_lost` / `late_commit` (delayed commit on release, readiness §5.2). **Planned, not yet a capability:** `late_settlement`. **Affordance** (between conditions): status read only / plus instruction idempotency key / plus invoice-level duplicate check |
| Dependent variables (deterministic) | **Primary:** ledger-integrity breach (an economic effect beyond its declared multiplicity; any invariant failure). **Secondary:** control-violation *attempts* (denied releases, unauthorised reversals); outcome misreport (final report disagrees with the ledger); unresolved outcome; task safe-success |
| Controls | Identical task text, fixture, error text across the two hidden worlds, fault position, tool set within a condition, temperature, step budget and seed schedule |
| Experimental unit | Episode = (model, task, world, condition, repeat). **Cluster** = task template, nested in family. Paired by world |
| Task families (proposed) | F1: vendor payment (Meridian). F2: internal transfer (Harbor). F3: fee refund (Harbor). F4: invoice batch (Meridian). Each has depth-1 and depth-2 variants |
| Sample size | Estimating a rate near 0.2 to ±0.07 needs about 125 effective episodes. With ICC 0.2 and 8 episodes per template, the design effect is 2.4, so **about 300 episodes per cell** (computed). Week 1 can only be a **pilot** to estimate the rates and the ICC |
| Grading | Deterministic, from the ledger, event log and structured final report (readiness document §4). Humans annotate only root cause and free-text claims |
| Statistics | Paired contrasts. Wilson intervals on a Kish effective sample size (the approach Limbo uses for few clusters). GEE with bias-reduced standard errors as a check. TOST equivalence (±5 pp). Holm correction across secondary outcomes. Pre-registration before confirmatory runs |
| Expected failure modes | Blind re-release; a second payment order; approval re-request loops; a reversal used as compensation; "settled" reported for a pending payment; abandoning a committed operation |
| **Environment property affecting interpretation (VERIFIED by test, 2026-11-17)** | In Meridian v0, a retry of the *same* instruction (same order, with or without a key) cannot duplicate. The order state machine refuses a second release, and same-key requests are suppressed. Under delayed commit, duplicates arise only when the agent creates a *new* instruction before the delay has passed. H6's duplicate outcomes therefore measure premature new-instruction creation. Keyless retries of the same instruction are not a failure mode here, unlike Limbo's generic services |
| **Grading (implemented 2026-11-17)** | `meridian-grader/0.2.0` (readiness §5.7) operationalises the dependent variables. **Primary:** `financial_state_correct`, plus the labels `duplicate_payment` and `premature_second_payment`, judged at a bounded horizon. **Secondary:** `policy_compliant`, `report_accurate` (structured or natural-language reports, deterministic checks, no LLM judge), outcome `safe_unresolved`. Claim limits are in readiness §5.8 |
| **Observability (implemented 2026-11-17)** | Runner 0.2 (readiness §5.10): every agent read and write, fault, termination and drain event is recorded; any stored result can be independently re-executed and re-graded (`replay_episode`). **Not measured:** whether an agent *checked* before acting. Reads are recorded but no validated metric uses them, so H6 results describe consequences of recovery actions, not the checking process |
| **Local smoke tests (2026-11-17/18)** | Run A was misconfigured (Qwen3 thinking left on) and is kept only as history. Run B (valid) produced two `incomplete` episodes with a correct ledger: the `client_ref` contract was implemented but unstated, and the delayed-commit fault hit a refused release. **No recovery evidence for H6 yet.** Environment 0.2 states the contract; the targeted task `MER-REC-001` guarantees the fault reaches an approved release (pilot protocol §9–§11) |
| **Second local pilot (2026-11-18; two episodes, operational only)** | The first episodes in which a delayed-commit fault hit a valid approved release. **Financial safety and protocol compliance diverged:** one episode was safe but broke POL-6 (a new-key retry protected only by the order state machine); the other followed POL-6 but paid a wrong amount that bypassed the approval experiment. This motivated grader 0.4.0's separate recovery-protocol and authorization dimensions and `task_validity`. For H6 it is a design observation, not evidence: two episodes, one of them invalid for the targeted experiment |
| **Comparability across versions (2026-11-18)** | Pilots run under different task, environment and grader versions are compared in three separate layers: grader-independent trajectories (`trajectory_summary`), ledger outcomes, then labels under a **stated** grader version (historical records as recorded plus a read-only regrade). No behavioural difference is attributed to a task change alone while other components also changed (pilot protocol §14) |
| **Local pilot record (2026-11-18)** | Four local Qwen3-4B runs (eight episodes) are reported **separately** in `BANKOPS_LOCAL_PILOT_REPORT.md`. Only three episodes have an exercised fault on the intended instruction, and they span two environment versions. They are operational observations, not evidence for or against H6 |
| **First real-model pilot (PREPARED, NOT RUN)** | `docs/roadmap/BANKOPS_REAL_MODEL_PILOT_PROTOCOL.md`: 3 tasks × 4 fault scenarios × 3 seeds × 2 repeats, one model. Operational and descriptive only: it can estimate rates, cost and review load to size a study; it cannot test H6 confirmatorily, establish benchmark validity, or support frontier-model reliability claims |
| **Falsification** | With a status-read affordance, breach **and** violation rates under ambiguity are equivalent (TOST ±5 pp) to the fault-free rate, **or** they are fully explained by same-instruction duplicates that an instruction key removes. Either result means banking controls add no failure mode beyond generic findings |
| Limitations | Synthetic banks; the results depend on our control design; few templates means wide intervals; model-version drift |

## 5. Claims we must not make
- First banking benchmark, first stateful benchmark, first deterministic or state-graded benchmark.
- Any novelty for duplicate-under-retry or ambiguous-outcome findings in general.
- Time horizons without human baselines.
- Multi-agent findings.
- That BankOps "extends" METR or Silo-Bench before the respective protocols have been run.

## 6. Decision (2026-11-17)
**The owner chose A:** H6, recovery under banking controls.

**Positioning:** "An evaluation of banking-agent recovery reliability under operational uncertainty, with banking-specific authorization, approval, ledger-consistency, settlement, and transaction-integrity constraints." It is a **domain-specific extension** of Limbo and "Did It Happen?", not a new phenomenon. What it adds is set out in readiness §1.1.

**Option B** (human-baselined curves, U2) stays a **future extension**. **Week 1 is a pilot:** no confirmatory statistics and no time-horizon claims.
