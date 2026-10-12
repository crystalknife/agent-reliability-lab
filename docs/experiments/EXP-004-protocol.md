# EXP-004 — Batch-Handling Comparison (Protocol Draft; not approved, not run)

Status: **DRAFT — NOT APPROVED FOR EXECUTION.** No EXP-004 artifacts
exist. Do not treat anything below as results. This document specifies
the experiment only; the required harness change is described, not
implemented, and no model invocation is authorized by this draft.

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
uncertainty interval for the difference excludes zero (method in §9).
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
  `batch`, exact flag name fixed at implementation review). One model
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

## 4. Required implementation changes (specified, not implemented)

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
   flag (proposed `--exec {sequential,batch}`, name fixed at review),
   record mode and per-trial model-request / tool-call counts on every
   result row. Append-only artifact behavior unchanged.
4. Error semantics preserved: unknown tool still raises `ValueError`
   from `dispatch` (tool_error termination); error-dict observations
   still non-terminal; model exceptions still `model_error`. The batch
   path must define, before execution, whether one failing call aborts
   the remaining calls in that turn (proposed: abort the turn, record
   all completed steps, terminate per current rules — fixed at review).

Out of scope: concurrency/threads, prompt or schema changes, new tools,
evaluator changes, budget redefinition.

## 5. Task selection

Retain all eight existing tasks (`src/minilab/tasks.py`) for
EXP-003 comparability and pass-rate context. `horizon-checking-combined`
is the only empirically batch-eliciting task (LangChain path, 5/5
repeats in EXP-003); spontaneous batching elsewhere is model behavior
and not guaranteed. Add one or two new multi-lookup tasks designed to
invite independent parallel calls (e.g. aggregating balances or
activity across several customers/accounts in one response), reusing
existing tools only. Each new task requires: ground-truth oracle,
`max_steps` set to oracle length plus slack per the `tasks.py`
convention, `validate_task` conformance, and review before use. Final
task list (8 vs 9–10) is fixed at protocol approval, not here. New
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

Before the full run, execute a small elicitation pilot (proposed: the
candidate new tasks plus `horizon-checking-combined`, 2–3 repeats each,
sequential mode only, reserved pilot paths per §13) to estimate the
model's multi-call emission rate through the harness prompt path.

Feasibility gate (predeclared): proceed to the full experiment only if
batches are emitted in a non-trivial share of pilot trials (exact
threshold fixed at approval; proposed: ≥20% of pilot trials on at
least one task). If the gate fails, report EXP-004 as **infeasible /
inconclusive** — never as evidence that batch handling has no effect —
and stop. Pilot trials are never merged into full-run results.

Rationale for the proposed floor: the primary analysis covers all
valid trials, so any batch-conditional effect is diluted by the
elicitation rate — at 20% elicitation only ~8 of 40 trials per arm
exercise batch handling, and a Wilson 95% interval on 8 trials spans
roughly ±28 points. The ≥20% proposal is therefore a floor for
informativeness, not a guarantee of resolving power: clearing it means
only large batch-conditional effects can move the primary estimate
beyond pilot uncertainty. With 2–3 repeats per pilot task the gate is
coarse by construction (a single batch clears it), so it functions as
a floor against zero elicitation; the approval review must carry the
pilot rate estimate, with its wide interval, into the full-run sizing
decision rather than treating a bare pass as adequate. Near the floor,
the run must be resized toward eliciting tasks or abandoned; below the
floor, it is abandoned per the gate.

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
- Report a pre-specified interval for the *difference* in pass rates
  (proposed: Newcombe's Wilson-based interval for the difference of
  two independent proportions; exact method fixed at approval before
  execution). **Confidence-interval overlap is not a hypothesis test
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

Provisional plan, same pilot scale as EXP-003 for comparability: all
approved tasks × 5 repeats per arm (40/arm at 8 tasks; 45–50/arm if new
tasks are added). At 10/40 per arm the Wilson 95% interval spans roughly
26 points, so only large effects are resolvable — this is a pilot, and
no statistical power is claimed without explicit assumptions, which are
not made here. The count stays provisional until task count, expected
effect size, observed batch-elicitation frequency (from §6), and
uncertainty goals are jointly justified at approval. If elicitation is
rare, the informative denominator is batch-occurring trials, and the
full run must be resized or abandoned rather than run blindly at 40/arm.

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
  continuation to them. Interrupted runs follow an EXP-003 §8-style
  recovery procedure (preserve partials unchanged, inventory,
  new uniquely named continuations only where remaining slots are
  expressible without rerunning completed trials, provenance retained,
  shortfalls disclosed) — the exact text is fixed at approval.
- Stop conditions: server crash, model swap/unavailability, artifact
  corruption (duplicate run_ids, unparseable lines, mismatched
  result/trajectory pairing), or any prompt to deviate from the
  approved commands.

## 12. Artifact naming, append-only rules, no-rerun protections

Pilot and full-run paths are reserved and verified absent before
execution; result files open in append mode, so never relaunch against
existing files. Final filenames are chosen at protocol approval, not in
this draft (proposed stems: `results/exp004_pilot_*` and
`results/exp004_minilab_seq_*` / `results/exp004_minilab_batch_*` with
matching `*_traj` files). No result or trajectory files are created by
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
3. Final task list, trial count, difference-interval method, elicitation
   threshold, and artifact filenames approved in a revised protocol;
   this draft's `DRAFT` status lifted explicitly.
4. Pre-flight green: clean `main` at recorded HEAD, server health +
   model confirmation, adapter smoke test (pre-flight only, never a
   trial), reserved paths absent, `python -m unittest discover -s
   tests` green.
5. Elicitation pilot passes the §6 gate; otherwise stop as infeasible.
6. Full run Arm A → validate → Arm B, per approved commands only.

## 15. Handoff

To continue without this conversation: this document, the EXP-003
protocol (§§3–6 for harness context) and results report (§§5–6 for the
batch observations motivating this design), the source files cited
above, and a clean checkout at the HEAD recorded in the run log. Open
questions: exact `--exec` flag name, batch-turn error-abort rule, new
task definitions, elicitation threshold, difference-interval method,
final filenames and trial count — all deferred to approval by design.
