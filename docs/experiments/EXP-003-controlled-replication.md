# EXP-003 — Controlled Framework Replication (Results)

Status: **COMPLETE. RESULTS.** Follows the approved
`docs/experiments/EXP-003-protocol.md`. This document reports only what the
four recorded artifacts contain; it does not re-run anything.

Artifacts analyzed (unchanged, read-only):

| Arm | Results | Trajectories |
|-----|---------|--------------|
| A — custom MiniLab harness (`normal`) | `results/exp003_custom_8task_5x.jsonl` | `results/exp003_custom_8task_5x_traj.jsonl` |
| B — LangChain (`schema_parity`) | `results/exp003_langchain_schema_parity_8task_5x.jsonl` | `results/exp003_langchain_schema_parity_8task_5x_traj.jsonl` |

Recorded SHA-256 (verified against baseline after drafting):

```
A res  1b8b4f381c96cb0e74289d32944bf807bd7b29f605890620684011dd98699dc6
A traj 190baf27e589308ba82fe00b0604c975786ac2533626d1fcd56d158e63a1c632
B res  abf68d1f7a6e30aaad15fd1ef520c7b6088de73bb686dcb0ff5bdd190137caae
B traj f8684b786ffedb6ec245c5e3245e18e0adb5c6b2bfe4d4e42202c28bc5bf0f30
```

## 1. Research question, configuration, and non-claims

**Question** (protocol §1): once model-facing tool schemas are equated, does
the observed pass-rate gap between the custom MiniLab harness and the
LangChain arm persist at higher repeat counts?

**Configuration** (protocol §3): Arm A = custom MiniLab harness
(`--framework minilab`, strategy `normal`); Arm B = LangChain
(`--strategy schema_parity`). Model `local:qwen3-4b` (external llama-server),
temperature 0, all eight MiniBank tasks, per-task `max_steps` unchanged,
5 repeats per task (recorded seed labels 0–4), 40 trials per arm, 80 total.
Identical tasks, environment, tools, evaluator, model, and temperature across
arms.

**Explicit non-claims** (protocol §2): this pilot does **not** establish
general framework superiority; n = 40 per arm is pilot-scale; it does **not**
claim full orchestration parity (parallel tool-call handling,
malformed-argument paths, unknown-tool paths, and budget accounting differ by
design, protocol §5/§7); and it does **not** guarantee statistical trial
independence beyond separate model invocations with recorded labels.

## 2. Per-arm accounting

Attempted = completed trials recorded. Valid = rows with
`trial_status="valid"`. Provider/infrastructure errors = rows with any other
status. Pass rate is among valid trials (protocol §9).

| Arm | Planned | Completed/attempted | Valid | Provider/infra errors | Passed | Pass rate (valid) |
|-----|--------:|--------------------:|------:|----------------------:|-------:|------------------:|
| A — custom MiniLab | 40 | 40 | 40 | 0 | 10 | 25.0% |
| B — LangChain | 40 | 40 | 40 | 0 | 10 | 25.0% |

Both arms recorded `trial_status="valid"` for all 40 rows; the valid-trial
denominator equals the attempted count. No provider or infrastructure failure
was recorded in either arm, so no trial was excluded from the primary metric
for validity reasons.

## 3. Wilson 95% confidence intervals

Computed from the valid-trial numerator/denominator (protocol §10):

| Arm | Passed / valid | Pass rate | Wilson 95% CI |
|-----|---------------:|----------:|---------------|
| A — custom MiniLab | 10/40 | 25.0% | 14.2% – 40.2% |
| B — LangChain | 10/40 | 25.0% | 14.2% – 40.2% |

The two intervals are identical and overlap broadly. The interval spans
approximately 26 percentage points (14.2%–40.2%) and does not include 50%; it
reflects substantial uncertainty at this sample size.

## 4. Per-task, per-difficulty, failure, termination, and repeat-index results

### 4.1 Per-task pass rates (valid trials)

| Task | Arm A | Arm B |
|------|------:|------:|
| `calc-total-c101` | 5/5 | 5/5 |
| `retrieval-bal-a103` | 5/5 | 5/5 |
| `horizon-checking-combined` | 0/5 | 0/5 |
| `policy-attr-c102` | 0/5 | 0/5 |
| `temporal-deposit-a103` | 0/5 | 0/5 |
| `waiver-c101` | 0/5 | 0/5 |
| `waiver-c102` | 0/5 | 0/5 |
| `waiver-c103` | 0/5 | 0/5 |

Observation: **both arms passed exactly the same two tasks
(`calc-total-c101`, `retrieval-bal-a103`) in all five repeats and failed the
other six in all five repeats.** No per-task pass-rate difference between arms
was observed.

### 4.2 Per-difficulty pass rates (valid trials)

| Difficulty | Arm A | Arm B |
|------------|------:|------:|
| easy | 10/20 | 10/20 |
| medium | 0/15 | 0/15 |
| hard | 0/5 | 0/5 |

Both arms used the same difficulty mix (easy 20, medium 15, hard 5). All passes
occur in the easy tier; no medium or hard task passed in either arm.

### 4.3 Failure taxonomy (valid trials; passes counted as `none`)

| Failure category | Arm A | Arm B |
|------------------|------:|------:|
| `evidence_gap` | 20 | 15 |
| `policy_misread` | 10 | 15 |
| `none` (passed) | 10 | 10 |

Observation: the only between-arm difference in the taxonomy is on
`horizon-checking-combined`, which is labelled `evidence_gap` in Arm A and
`policy_misread` in Arm B (see §6). All other task labels are identical across
arms.

### 4.4 Termination distribution

| Termination reason | Arm A | Arm B |
|--------------------|------:|------:|
| `agent_final` | 40/40 | 40/40 |

Every trial in both arms terminated with `agent_final`. No trial exhausted its
step budget (see §5.3), so no truncation-driven termination occurred.

### 4.5 Recorded repeat-index (seed-label) descriptive rates

| Recorded seed label | Arm A | Arm B |
|---------------------|------:|------:|
| 0 | 2/8 | 2/8 |
| 1 | 2/8 | 2/8 |
| 2 | 2/8 | 2/8 |
| 3 | 2/8 | 2/8 |
| 4 | 2/8 | 2/8 |

Limitation (protocol §6): these seed values are **grouping metadata only**.
Neither arm transmits a seed to local inference (temperature is the only
sampling parameter sent). The labels identify repetitions for analysis; they
do not control the sampler, and they do not establish seed effects or
independent sampling. Reporting them as 2/8 per label is descriptive and
carries no inferential weight.

## 5. Parallel tool calls and protocol differences

### 5.1 Observed parallel batches

Observation, **Arm B (LangChain)** only. The recorded `AIMessage.tool_calls`
lengths across all 40 trajectories were `{0: 40, 1: 90, 7: 5}` — i.e. 90
single-call turns, 40 zero-call turns (final answers), and **five turns
containing a seven-call batch**. Those five batches occur one per repeat on
`horizon-checking-combined` (recorded seed labels 0–4), each with the same
call set:

```
get_customer, get_customer, get_account, get_account,
get_transactions, get_transactions, calculate
```

That is **5 of 40 Arm B trials (12.5%)** containing a multi-call batch. All
other Arm B trials were single-call.

**Arm A (custom harness) batch frequency is not directly comparable.** The
harness records one parsed tool object per assistant turn and executes only
the first call (protocol §5/§7); across 166 assistant turns every recorded
object was a single tool dict, so its underlying batch size is not observable
from the artifacts. The two arms therefore cannot be compared on batch
frequency.

### 5.2 Paths with no exercised cases (unexercised limitations)

The artifacts contain **no evidence** of malformed-argument handling or
unknown-tool handling in either arm: no recorded failure reason, observation,
or trajectory text references malformed/invalid arguments or an unknown tool.
These paths were not exercised in EXP-003, so this experiment provides no
empirical measurement of them. They remain documented, known orchestration
differences (protocol §5), not results.

### 5.3 Budget accounting

No trial reached its `max_steps` (e.g. `waiver-c101` max 8, observed 4–5;
`horizon-checking-combined` max 9, observed 7). Because no trial hit the
budget boundary, EXP-003 did not exercise the recursion-unit-versus-model-call
budget-accounting difference. Recorded valid-trial step-count distributions:

| Steps | Arm A | Arm B |
|-------|------:|------:|
| 1 | 10 | 10 |
| 2 | 10 | 10 |
| 4 | 14 | 15 |
| 5 | 1 | 0 |
| 7 | 5 | 5 |

The single distributional difference is the one Arm A 5-step trial described
in §6.2. Aggregate action counts were otherwise matched (e.g.
`get_transactions`: 26 in Arm A vs 25 in Arm B; `search_policy`, `get_account`,
`get_customer`, `calculate` identical).

## 6. Representative divergences

### 6.1 `horizon-checking-combined` failure-label divergence

Both arms failed this task 0/5, so this is **not** a reliability difference.
Recorded evidence:

- **Arm B** executed the task as a single seven-call batch (recorded seed
  labels 0–4) and returned a final answer stating the combined checking balance
  as **$3000.00**, while the recorded `calculate` observation grounded 2600.
  Its recorded reasons are `verdict mismatch`, missing evidence
  `['2600','a101','a103']`, and ungrounded numbers `[2600]`; the trial was
  labelled `policy_misread`.
- **Arm A** executed the same seven actions sequentially (`get_customer`,
  `get_customer`, `get_account`, `get_account`, `get_transactions`,
  `get_transactions`, `calculate`), returned **$2,600**, and was labelled
  `evidence_gap` with reason missing evidence `['a101','a103']`.

Interpretation (cautious): the divergent label is consistent with the two
loops producing differently structured final answers on a task neither arm
solved; the batches in Arm B are the most visible structural difference in the
recorded trajectory. This documents a taxonomy difference on a failed task and
does not imply any reliability advantage for either arm.

### 6.2 `waiver-c101` step-count difference

Arm A recorded one trial (recorded seed label 0) with 5 steps, including a
`get_transactions` call; the other four Arm A repeats and all five Arm B
repeats used 4 steps. Both arms nevertheless failed all five repeats with the
same `evidence_gap` verdict, and both produced a final citing 1500 (Arm A's
seed-0 trial additionally flagged as ungrounded numbers `[1500]`). This is the
sole step-count difference between arms and did not change any pass/fail
outcome.

Other failed tasks (`policy-attr-c102`, `temporal-deposit-a103`,
`waiver-c102`, `waiver-c103`) showed matching failure mechanisms across arms
in the recorded evidence.

## 7. Limitations

- **Pilot scale.** 40 valid trials per arm; Wilson 95% CIs span ~±13
  percentage points and overlap broadly. The observed equal rates are
  compatible with materially different underlying rates.
- **Uncertainty.** No significance language is used; the equal point estimates
  of 25.0% carry the uncertainty above.
- **Recorded seed labels are metadata only.** They are not transmitted to
  inference and do not establish seed effects or independent sampling.
- **Incomplete orchestration parity** (by design): parallel multi-call
  handling, malformed-argument path, unknown-tool path, and budget accounting
  differ between arms. Of these, only parallel batching was observable, and
  only in Arm B; the malformed-argument and unknown-tool paths and the
  budget-limit behavior were **not exercised** in this run.
- **Single evaluator view.** Pass/fail and failure labels are taken as
  recorded by the evaluator; no independent re-scoring was performed.

## 8. Conclusion

In this pilot, both arms completed 40/40 planned trials, all valid, with zero
recorded provider/infrastructure errors. Each arm passed 10/40 valid trials
(25.0%; Wilson 95% CI 14.2%–40.2%), passing the same two tasks in all five
repeats and failing the other six in all five repeats. On pass/fail outcomes
the two arms were therefore identical per task. The one divergence was not in
pass/fail but in the failure classification and accompanying final answer on
the already-failed task `horizon-checking-combined`: Arm B was labelled
`policy_misread` with a final answer of $3000.00, while Arm A was labelled
`evidence_gap` with $2,600 (Arm B also recorded a seven-call batch there).
That is a taxonomy and trajectory difference on a task both arms failed, not
an overall reliability difference.

These equal observed pass rates **establish neither equivalence nor
superiority** between the two orchestration loops. At pilot scale, with wide
overlapping intervals, known and partly unexercised orchestration differences,
and grouping-only seed labels, the result is a descriptive match on this task
set under schema parity — not a framework comparison. Any outcome here remains
context for larger, better-powered work, not a general claim.
