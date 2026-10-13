# EXP-004 — Batch-Handling Comparison (Approved protocol; not run)

Status: **APPROVED PROTOCOL. EXPERIMENT NOT AUTHORIZED.** No EXP-004 artifacts
exist. Do not treat anything below as results. This document specifies
the experiment only; the required harness change is implemented in the
working tree and is subject to separate code review before any trial,
and no model invocation is authorized by this approval.
Pilot, extension, recovery, and main-run trials each require separate
explicit authorization.

## 1. Research question and falsifiable hypothesis

Question: does processing all tool calls emitted in one assistant
response, rather than processing only the first call, change task
reliability when the harness, model, prompt, tool schemas, fixtures,
evaluator, and model-call budget are held constant?

This is **not** a parallel-versus-sequential execution experiment.
The proposed batch implementation executes calls in listed order using
the existing synchronous `dispatch`, and current tools are stateless
reads (`src/minilab/env.py`, `src/minilab/tools.py`), so execution
order cannot change observations. True concurrency effects are outside
this experiment's scope. What varies between arms is batch *handling*:
how many emitted calls are executed per model turn and hence what
enters the context — not concurrency.

Falsifiable hypothesis (H1): the batch-handling arm's pass rate among
valid trials differs from the first-only arm's by an amount whose
uncertainty interval for the difference excludes zero (method in §8).
Null (H0): any observed difference is within pilot uncertainty.
A null batch-elicitation rate (see §7) renders the trial
*inconclusive*, never evidence of no effect.

## 2. Rationale and lessons from EXP-003

EXP-003 (`docs/experiments/EXP-003-controlled-replication.md`) compared
the custom harness against LangChain under schema parity: both arms
recorded 10/40 valid-trial passes (25.0%, Wilson 95% CI 14.2%–40.2%),
passing the same two tasks and failing the same six. The sole
between-arm divergence was on `horizon-checking-combined`, where
LangChain executed a seven-call batch per repeat while the harness
executes at most one call per turn — but the harness *silently
truncates* multi-call emissions (adapter keeps `tool_calls[0]` only),
so its true batch-emission rate is unobservable in EXP-003 artifacts.
Framework, prompt framing, and batch handling were therefore confounded
on exactly the task where the arms diverged. EXP-004 isolates the one
factor that can be isolated inside a single harness: batch handling.

## 3. Arms and exact controlled variables

- Arm A (control): existing first-call-only behavior (`--framework
  minilab`, strategy `normal`, execution mode `sequential`). Status quo
  `run_agent` loop, unchanged.
- Arm B: same harness with all-calls-per-turn handling (execution mode
  `batch` via `--exec batch`). One model
  request per turn in **both** arms; Arm B executes every emitted call
  in listed order through the existing synchronous `dispatch` and
  appends every observation before the next model request.
- No LangChain arm participates in the primary causal comparison.

Held constant: harness source (except the mode branch), model
(`local:qwen3-4b`), system prompt (single `build_system_prompt()`
source), model-facing tool schemas, task prompts, per-task `max_steps`
(model-call budget, unchanged semantics), environment/fixture,
evaluator, temperature (0), seed-label protocol.

Equivalence holds for initial conditions and the handling-rule
assignment: once the arms diverge (Arm B executing calls Arm A
discards), subsequent model inputs differ by construction. That
post-treatment divergence is the effect under study, not a confound;
the design isolates it by holding everything before the first
divergence constant. Realized model-request counts will also differ
endogenously (batch turns do more work per request); both counts are
recorded as covariates per §7.

## 4. Required implementation changes (specified; implemented in working tree, pending review)

Minimal proposed change, subject to separate code review before any
trial:

1. Adapter (`src/minilab/models/local.py` `make_local_model`): return
   the full emitted call list instead of silently keeping the first.
   No other adapter behavior changes.
2. `run_agent` (`src/minilab/agent.py`): accept either a single
   `{"tool"}` dict (unchanged path) or a `{"calls": [...]}` list plus
   an explicit execution-mode flag. `sequential` preserves today's
   first-only behavior as the default; `batch` dispatches each listed
   call in order, records one `Step` per call with correct
   observation-to-call matching, and consumes exactly one model-call
   budget unit per turn either way.
3. Runner (`src/minilab/runner.py`): expose the mode as an explicit CLI
   flag (`--exec {sequential,batch}`, locked; default `sequential`),
   record mode and per-trial model-request / tool-call counts on every
   result row. Append-only artifact behavior unchanged.
4. Error semantics preserved: unknown tool still raises `ValueError`
   from `dispatch` (tool_error termination); error-dict observations
   still non-terminal; model exceptions still `model_error`. The batch
   path must define, before execution, whether one failing call aborts
   the remaining calls in that turn (locked: abort the turn, record
   all completed steps, terminate per current rules).

Out of scope: concurrency/threads, prompt or schema changes, new tools,
evaluator changes, budget redefinition.

## 5. Task selection

Retain all eight existing tasks (`src/minilab/tasks.py`) for
EXP-003 comparability and pass-rate context. `horizon-checking-combined`
is the only empirically batch-eliciting task (LangChain path, 5/5
repeats in EXP-003); spontaneous batching elsewhere is model behavior
and not guaranteed. Locked main-run task list, in `--tasks all` order
(10 tasks): `waiver-c101`, `waiver-c102`, `waiver-c103`,
`retrieval-bal-a103`, `calc-total-c101`, `policy-attr-c102`,
`temporal-deposit-a103`, `horizon-checking-combined`,
`deposit-total-combined`, `retrieval-multi-checking`. The two new
multi-lookup tasks reuse existing tools only, each with ground-truth
oracle, `max_steps` per convention, and T1–T3 scripted validation
evidence (see §14). New
tasks must additionally keep evaluator support margins robust to
duplicated observations: `_observed_finances` (`src/minilab/
evaluation.py`) sums deposits across repeated `get_transactions`
observations, so a batch repeating a transaction lookup double-counts
deposits (balances use a mean and are duplication-invariant;
`grounded_numbers` and `required_tools` are set-based and unaffected).
Verify support stability under duplicated observations with scripted
trajectories before approval. Current tasks satisfy this (c103
100→200 stays below 500; c102 stays supported; c101 is average-based).

## 6. Elicitation pilot and feasibility gate

Before the full run, execute a small elicitation pilot (pool: the two
new tasks, `horizon-checking-combined`, and `waiver-c102` — 4 tasks × 4
repeats = 16 trials, sequential mode only, reserved pilot paths per
§12) to estimate the model's multi-call emission rate through the
harness prompt path. This pool is selected in the proposed design and
takes effect only upon formal protocol approval (§14). Sixteen is the minimal pilot size
at which zero observed emissions rules out the 20% floor (Wilson 95%
upper bound 19.4% at 0/16, versus 24.2% at 0/12, which cannot). All
sizes and thresholds in this section are locked values recorded for
review; they take effect only on formal protocol approval.

Definitions: a trial is batch-emitting iff its recorded full call list
contains at least one turn with more than one emitted call. The gate
uses the emission proportion pooled across the locked pilot task set:
the denominator is informative trials only — provider, evaluator, and
environment-error trials carry no emission information and are reported
separately, never counted as non-emissions. Let p̂ be the pooled
observed emitting share with Wilson 95% interval [L, U]. Per-task
emission rates are reported descriptively; the best-performing task
alone never decides PASS.

Feasibility outcomes (locked rules). Apply in order; the three outcomes are mutually exclusive:
- PASS: pooled p̂ ≥ 20%. Proceed to full-run sizing.
- FAIL: p̂ < 20% and pooled U < 20% — evidence the rate is below the
  floor. Report EXP-004 as infeasible and stop, never as evidence that
  batch handling has no effect.
- INCONCLUSIVE: p̂ < 20% and U ≥ 20% (pilot too small to tell).
  One optional extension of a fixed 16 additional trials on fresh
  reserved paths is permitted, under new filenames verified absent
  before the extension run and never appended to the initial pilot
  files; the extension pools with the original pilot by simple
  aggregation (same tasks, same sequential mode) and the rule is
  re-applied once. If the extension yields fewer than 16 informative
  trials, preserve and report the shortfall: the pooled counts still
  define p̂ and [L, U] — the wider interval then governs the re-applied
  rule — and failed trials are never counted as non-emissions. If still
  inconclusive, abandon as underpowered and stop without
  a main run.
Zero informative trials: if a pilot batch yields no informative trials
at all, p̂ and the Wilson interval are undefined and the batch is void —
classified neither as FAIL nor as zero emissions. After the
infrastructure issue is fixed, rerun that batch on fresh reserved paths
with new filenames verified absent (never appended to the voided
files); this void rerun does not consume the one statistical extension
allowance. Report the void batch and its failure reasons separately,
preserving the audit trail of both attempts.
Pilot trials are never merged into full-run results.

This gate is a feasibility screen, not a power calculation and not
evidence of treatment effectiveness: it answers whether the treatment
will occur often enough to study, and guarantees nothing about
resolving an effect — only large batch-conditional effects can move
the primary estimate at pilot N (see rationale below and §9).

Rationale for the proposed floor: the primary analysis covers all
valid trials, so any batch-conditional effect is diluted by the
elicitation rate — for illustration, at a hypothetical 20% elicitation
only ~10 of 50 trials per arm exercise batch handling, and a Wilson 95%
interval for a hypothetical 10/50 spans approximately 11.2%–33.0%.
This is an illustration, not an observed EXP-004 result. The ≥20%
proposal is therefore a floor for
informativeness, not a guarantee of resolving power: clearing it means
only large batch-conditional effects can move the primary estimate
beyond pilot uncertainty. At n=16 the gate is still coarse (a single
emitting trial is 6.25%, so the PASS/INCONCLUSIVE boundary rests on
few events); it functions as a floor against near-zero elicitation,
and the approval review must carry the pilot rate estimate, with its
wide interval, into the full-run sizing decision rather than treating
a bare pass as adequate. Near the floor, the run must be resized
uniformly or abandoned; below the floor, it is abandoned per the gate.

## 7. Primary outcome, secondary metrics, trial accounting

- Primary outcome: pass rate among valid trials per arm
  (`passed ÷ valid`, protocol §9 convention of EXP-003).
- Primary effect: absolute difference in pass rates, in percentage
  points (Arm B − Arm A).
- Secondary metrics: batch-elicitation rate per arm; pass rate
  conditional on batch occurrence (**descriptive only** — batch
  emission is model behavior and conditioning on it can introduce
  selection bias); per-task and per-difficulty tables; failure-taxonomy
  mix; termination distribution; per-trial model-request and tool-call
  count distributions; per-repeat-index ad-hoc rates using recorded
  seed labels.
- Trial accounting (required table per arm): planned, completed /
  attempted, valid, provider/infrastructure errors, evaluator errors,
  passed, pass rate among valid. Non-valid outcomes are reported
  separately, never silently discarded, never merged into the
  denominator.

## 8. Statistical analysis and uncertainty

- Report Wilson 95% intervals for each arm's individual pass rate.
- Report the pre-specified interval for the *difference* in pass rates:
  Newcombe's Wilson-based interval for the difference of
  two independent proportions (z = 1.96, no continuity correction),
  computed before execution. Both arms share the same task set and
  planned task/seed layout; seed values are metadata-only and do not
  themselves establish randomized or paired outcomes, and the outcomes
  are claimed as neither demonstrably independent nor paired. The
  independent-proportions interval does not model task-level dependence
  (see nominal-interval caveat below). **Confidence-interval overlap is not a hypothesis test
  and must not be used as one.** If the difference interval includes
  zero, report the comparison as inconclusive — compatible both with no
  effect and with material effects in either direction — never as
  evidence of no difference.
- No equivalence, superiority, or causal claims beyond what the design
  supports. Any causal language is restricted to the isolated factor
  (batch handling within one harness) and is conditional on the
  feasibility gate passing.
- Batch-conditional rates carry no inferential weight (selection-bias
  caveat in §7 applies).
- All reported intervals treat trials as independent Bernoulli draws
  and are therefore nominal: repeated trials on the same task and model
  may be correlated, so true uncertainty may be wider than stated. Do
  not over-read narrow-looking bounds.

## 9. Trial count and sample-size rationale (provisional)

Locked plan: all ten locked tasks × 5 repeats per arm = 50 planned
trials per arm. At 12/50 per arm, for example, the Wilson 95% interval spans roughly
23 points (14.3%–37.4%), so only large effects are resolvable — this is a pilot, and
no statistical power is claimed without explicit assumptions, which are
not made here. Repeat counts stay subject to the §6 sizing outcome
(resize uniformly or abandon); any change requires re-approval. If elicitation is
near the floor, the full run must be resized or abandoned rather than
run blindly at the planned 50/arm. The sizing heuristic uses the pooled Wilson
lower bound L from the §6 gate (same informative-trial denominator):
planned main-run trials per arm N should satisfy N × L ≥ ~8
batch-exercising trials per arm; otherwise scale repeats or abandon.
Resizing scales repeats uniformly across the
locked task list — tasks are never reweighted, so per-task repeat
counts stay equal and EXP-003 task-table comparability is preserved —
subject to a predeclared cap (proposed: at most twice the base repeats,
i.e. ≤10 per task; exact cap fixed at approval). Resizing changes N;
it does not change the task list, the estimand, or the accounting
rules.

## 10. Model configuration and inference-randomness limitations

Model `local:qwen3-4b` via the external llama-server (port 8081),
temperature 0. Temperature 0 does not guarantee deterministic behavior
(runner docstring convention). Recorded seed labels (`seed_used =
seed + rep`) are grouping metadata only: they are not transmitted to
local inference and do not control the sampler, establish seed effects,
or constitute independent sampling. Fresh `MiniBankEnv` per trial;
server health, loaded-model confirmation, adapter smoke test, green
test suite, and reserved-path absence checks follow the EXP-003
pre-flight pattern before any run.

## 11. Invalid trials, failures, and recovery policy

- `classify_trial` (`src/minilab/failures.py`) determines
  `valid` / `provider_error` / `environment_error` / `evaluator_error`;
  evaluator exceptions are caught per trial and recorded, never fatal
  to the run. Failure taxonomy (`classify`) applies to valid trials
  only, unchanged.
- Attempt all planned trials. Never silently retry or replace
  provider-failed trials; preserve every fault in the artifacts;
  report provider/infrastructure errors separately from agent
  reliability. Never overwrite existing artifacts or append a
  continuation to them. No continuation or rerun is automatic.
- Interrupted-run recovery, locked: preserve every partial result and
  trajectory file byte-unchanged, including failed, invalid, and voided
  attempts. Reconcile original artifacts and all attempts first —
  inventory completed trials, provider/infrastructure/evaluator
  failures, duplicate run identifiers, malformed JSONL lines, and
  result-to-trajectory pairing — and record a documented diagnosis of
  what stopped and what is missing. A continuation is possible only for
  genuinely unattempted whole-task slots expressible with the approved
  flags (`--tasks` subset) without rerunning any completed trial, under
  new filenames verified absent before launch, preserving `--exec` and
  every other locked flag; it is a possible, explicitly authorized
  continuation procedure, not a safe resume — the append-mode runner
  cannot resume an interrupted run in place. If task/seed slots,
  pairing, or completion status are ambiguous in any way, stop and
  report the shortfall instead. Cross-file duplicates are keyed by
  `(task_id, seed)`; all original artifacts remain immutable and every
  duplicate or conflicting attempt is preserved and disclosed —
  original-record precedence never determines validity or the
  informative denominator on its own. Inclusion follows the predeclared
  `classify_trial` statuses; if a conflict prevents reliable
  classification of the affected slots, stop and report the shortfall
  rather than choosing silently. Never replace an unfavorable,
  invalid, or incomplete completed attempt. This operational recovery
  is distinct from the single permitted statistical extension (§6),
  which adds separately authorized new planned trials rather than
  completing planned slots. Every continuation requires a documented
  reason and explicit human authorization recorded before launch (see
  §14); without it, report the batch as incomplete.
- Stop conditions: server crash, model swap/unavailability, artifact
  corruption (duplicate run_ids, unparseable lines, mismatched
  result/trajectory pairing), or any prompt to deviate from the
  approved commands.

## 12. Artifact naming, append-only rules, no-rerun protections

Pilot and full-run paths are reserved and verified absent before
execution; result files open in append mode, so never relaunch against
existing files. Locked artifact paths (all verified absent; re-verify
immediately before each run; names denote allocated capacity):
- Pilot: `results/exp004_pilot_seq_4task_4x.jsonl` and
  `results/exp004_pilot_seq_4task_4x_traj.jsonl`.
- Extension: `results/exp004_pilot_ext_seq_16x.jsonl` and
  `results/exp004_pilot_ext_seq_16x_traj.jsonl`.
- Arm A: `results/exp004_minilab_seq_10task_5x.jsonl` and
  `results/exp004_minilab_seq_10task_5x_traj.jsonl`.
- Arm B: `results/exp004_minilab_batch_10task_5x.jsonl` and
  `results/exp004_minilab_batch_10task_5x_traj.jsonl`.
No result or trajectory files are created by
this draft. Combining partial runs without a protocol-approved recovery
method is prohibited.

## 13. Risks, limitations, explicit non-claims

- Batch elicitation is model behavior and may be too rare to support a
  comparison (gated by §6).
- Concurrency per se is untestable here: tools are stateless reads, so
  this experiment cannot speak to true parallel-execution effects.
- Pilot scale with wide intervals; single model (`local:qwen3-4b`);
  seed labels non-controlling; evaluator is one deterministic view
  (no re-scoring).
- This experiment does **not** establish framework superiority, does
  **not** claim full orchestration parity, and does **not** guarantee
  trial independence beyond separate invocations with recorded labels.
- Batch-conditional comparisons are descriptive only (selection bias).

## 14. Approval and execution checklist

1. Harness mode-switch change implemented, reviewed, and covered by
   inference-free scripted-model unit tests (zero / one / multiple
   calls, ordering, observation matching, error handling, budget
   accounting), plus a regression test asserting sequential-mode
   trajectories are byte-identical to the pre-change implementation on
   scripted single-call sequences. Tests green.
2. New tasks (if any) reviewed with oracles and `max_steps`
   justification.
3. Design lock: the locked values in §§5, 6, 8, 9, 12 are confirmed
   as the approved design. Design lock authorizes no trials.
4. Formal protocol approval: GRANTED by explicit human statement
   recorded here; DRAFT is lifted. Approval alone authorizes no trials.
5. Pilot authorization: a separate explicit decision, granted only
   after pre-flight passes at the pinned execution checkout (clean
   tree at recorded HEAD, server health + model confirmation, adapter
   smoke test as pre-flight only, reserved pilot paths verified
   absent, full test suite green).
6. Pilot execution and gate evaluation: run the 16-trial pilot per
   approved commands only; compute pooled p̂ and [L, U]; apply the §6
   ordered rule; record PASS / FAIL / INCONCLUSIVE with counts.
7. Extension authorization and execution: permitted only on a recorded
   INCONCLUSIVE outcome, by separate explicit decision, on fresh paths
   verified absent; fixed 16 trials (same 4 tasks × 4 repeats,
   sequential mode), no early stopping on emerging emissions;
   re-apply the gate once to pooled counts.
8. Main-run authorization and execution: permitted only on a recorded
   PASS plus the N×L sizing review, by separate explicit decision;
   Arm A → validate → Arm B per approved commands only.
9. Recovery authorization: any continuation after an interrupted or
   failed batch requires a separate explicit decision with documented
   diagnosis and fresh verified-absent paths, granted only when §11
   permits continuation; otherwise report the shortfall. Recovery
   authorization is distinct from pilot, extension, and main-run
   authorizations and never substitutes for them.

## 15. Handoff

To continue without this conversation: this document, the EXP-003
protocol (§§3–6 for harness context) and results report (§§5–6 for the
batch observations motivating this design), the source files cited
above, and a clean checkout at the HEAD recorded in the run log. Open
questions remaining: none on design values (all locked above; formal
approval granted); remaining are process decisions — pilot
authorization, extension authorization (if gated),
and main-run authorization — each requiring a separate explicit
human statement. Locked values took effect on formal approval.
