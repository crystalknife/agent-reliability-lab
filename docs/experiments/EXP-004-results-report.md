# EXP-004 — Batch-Handling Comparison (Results)

Status: **COMPLETE. RESULTS.** Follows the approved
`docs/experiments/EXP-004-protocol.md`. This document reports only what the
recorded artifacts contain; it does not re-run anything.

## 1. Executive summary and research question

Question (protocol §1): does processing all tool calls emitted in one
assistant response, rather than processing only the first call, change
task reliability when harness, model, prompt, tool schemas, fixtures,
evaluator, and model-call budget are held constant? This is a
sequential batch-handling comparison, not a concurrency experiment:
both arms dispatch through the same synchronous `dispatch`, and all
tools are stateless reads.

Result: Arm A (first-only) passed **20/50 valid trials (40.0%)**;
Arm B (all-calls) passed **20/50 (40.0%)**. Difference
B − A = **+0.0 percentage points**, Newcombe-Wilson 95% interval
**−18.6 to +18.6 points**, which includes zero: the comparison is
**inconclusive** — compatible with no effect and with material effects
in either direction, and not evidence of no difference. Per-task
pass/fail outcomes were identical across arms on all ten tasks.

## 2. Approved design and locked variables

- Arms: A = `--exec sequential` (first call only); B = `--exec batch`
  (all emitted calls, listed order, synchronous dispatch, one
  model-request budget unit per turn either way). Same harness
  (`src/minilab/agent.py` at pinned commit), same model
  (`local:qwen3-4b`, external llama-server, temperature 0), same system
  prompt, tool schemas, fixtures, evaluator, per-task `max_steps`.
- Task set (10, `--tasks all` order): `waiver-c101`, `waiver-c102`,
  `waiver-c103`, `retrieval-bal-a103`, `calc-total-c101`,
  `policy-attr-c102`, `temporal-deposit-a103`,
  `horizon-checking-combined`, `deposit-total-combined`,
  `retrieval-multi-checking`. Seeds 0–4 (grouping labels only, not
  transmitted to inference). Repeats: pilot 4/task; main 5/task/arm
  (50 planned trials per arm, 100 total).

## 3. Pilot findings and gate decision

Pilot (`results/exp004_pilot_seq_4task_4x{,_traj}.jsonl`): 16 attempted,
16 valid, 0 provider/infrastructure/evaluator errors → 16 informative
trials. Batch-emitting trials: 12/16 = **75.0%**, Wilson 95% interval
**50.5%–89.8%**. Per-task (descriptive): `deposit-total-combined` 4/4,
`horizon-checking-combined` 4/4, `retrieval-multi-checking` 4/4,
`waiver-c102` 0/4. Both new tasks elicited batches on every trial.
Gate: pooled p̂ = 75% ≥ 20% → **PASS** (no extension needed; none run).
Sizing check: N × L = 50 × 0.505 ≈ 25 ≥ 8 → base plan retained with no
resizing.

## 4. Main-run results

| Arm | Planned | Completed | Valid | Provider/infra/evaluator errors | Passed | Pass rate (valid) |
|-----|--------:|----------:|------:|-------------------------------:|-------:|------------------:|
| A — sequential | 50 | 50 | 50 | 0 | 20 | 40.0% |
| B — batch | 50 | 50 | 50 | 0 | 20 | 40.0% |

Per-task passed/valid (identical in both arms): `calc-total-c101` 5/5,
`deposit-total-combined` 5/5, `retrieval-bal-a103` 5/5,
`retrieval-multi-checking` 5/5; `horizon-checking-combined`,
`policy-attr-c102`, `temporal-deposit-a103`, `waiver-c101`,
`waiver-c102`, `waiver-c103` 0/5 each. Terminations: `agent_final`
50/50 in both arms. Failure taxonomy (valid): A `evidence_gap` 20 /
`policy_misread` 10 / `none` 20; B `evidence_gap` 15 /
`policy_misread` 15 / `none` 20 — the sole split is
`horizon-checking-combined` (A `evidence_gap`, B `policy_misread`),
the same task that diverged in EXP-003, with both arms failing it 0/5.

## 5. Primary effect estimate

Newcombe-Wilson 95% interval for the difference (z = 1.96, no
continuity correction, per protocol §8): **+0.0 points, 95% interval
−18.6 to +18.6**. Individual Wilson 95% intervals: 27.6%–53.8% per arm.
Interpretation: the interval includes zero and spans roughly ±19
points; the data are consistent with no reliability difference and
with substantial differences in either direction. This is an
inconclusive result, not evidence of equivalence, superiority, or
absence of effect, and it makes no causal claim beyond the isolated
batch-handling factor under the stated limitations.

## 6. Validity, errors, and artifact integrity

- Trial statuses: pilot 16/16 `valid`, main run 100/100 `valid`
  across both arms — 116/116 valid across all three runs; zero
  provider, infrastructure, or evaluator errors; nothing excluded from
  any denominator.
- Run IDs: 50 unique per main-run file, 16 unique in the pilot, 1:1
  result↔trajectory pairing throughout, zero cross-arm ID overlap,
  distinct experiment IDs.
- Metadata uniform per file: framework `minilab`, correct `exec_mode`
  (`sequential` / `batch`), model `local:qwen3-4b`, temperature 0.
- Coverage: complete task × repeat grids (pilot 4×4, main 10×5), no
  duplicates, no malformed JSONL rows.
- Recorded SHA-256 (full hashes, verified against the artifacts;
  no separate run log exists):
  pilot results `c12d276a2ebd387d69addf52baabe1f0d00c4dbf769f49a789a19942553470e3`,
  pilot trajs `1f4684d2929d362189bfd15864c107da73cb9cb059dea71e1a4b460ca0923e19`,
  Arm A results `b7fd61d22b5db6e9954eb5756ee11aa02557e6d08c2f0bf9dc864d609f035dda`,
  Arm A trajs `5e805a5164bb4014c0c62223083ed89224bfabdc7848938a92a9eb7ef67cee92`,
  Arm B results `9cf5583220ee89473e02ad4c36acf568b8ec3f3485789d7269c3f41e5e46410f`,
  Arm B trajs `84350e42d6285d2e8fdfc6347806918f8e178200ec1d85cf012276dff43abf7c`
  (artifacts preserved byte-unchanged).

## 7. Secondary batch-emission findings (descriptive, not the outcome)

Main-run emitting trials (≥1 multi-call turn): Arm A 15/50, Arm B
15/50 (both new tasks 5/5, horizon 5/5, all others 0/5 in Arm B).
Arm A emissions were recorded but truncated to the first call per the
sequential rule; Arm B executed them. Emission differences between arms
reflect post-treatment contexts and carry no inferential weight
(selection-bias caveat per protocol §7).

## 8. Limitations

- Pilot scale (n=50/arm; pilot n=16): wide intervals; only large effects
  resolvable; no power was claimed.
- Both arms share the task set and task/seed layout; seed labels are
  metadata-only and establish neither randomization nor pairing; all
  intervals are nominal and true uncertainty may be wider.
- Single model (`local:qwen3-4b`), single temperature, fixed task suite:
  no generalization to other models, samplers, or domains.
- Stateless read-only tools: this experiment cannot speak to true
  concurrency effects (§1 scope).
- The inconclusive primary interval establishes neither equivalence nor
  absence of effect. The evaluator is one deterministic view.

## 9. Deviations and recovery history

None. Both arms ran once to completion with no server faults, no
interruptions, no retries, replacements, repairs, or continuations; no
extension was needed or run. All recorded counts match planned counts.

## 10. Conclusion and reproducibility

Batch handling (all-calls vs first-only) showed an observed difference
of 0.0 points with a 95% interval of −18.6 to +18.6 on this task set
under the controlled conditions — an inconclusive pilot-scale result.
The only between-arm divergence was the failure label on the already
failed `horizon-checking-combined`, replicating the EXP-003 pattern
without changing any pass/fail outcome.

Reproduce from: pinned commit
`c46254255b10b8e5b97a121911ba8eed62f3b458` (tag `exp004-execution`);
artifacts `results/exp004_pilot_seq_4task_4x{,_traj}.jsonl`,
`results/exp004_minilab_seq_10task_5x{,_traj}.jsonl`,
`results/exp004_minilab_batch_10task_5x{,_traj}.jsonl`; protocol
`docs/experiments/EXP-004-protocol.md`; commands recorded in the run
log (Arm A `--exec sequential`, Arm B `--exec batch`, otherwise
identical: `--framework minilab --model local:qwen3-4b --tasks all
--repeats 5 --seed 0 --temperature 0`).
