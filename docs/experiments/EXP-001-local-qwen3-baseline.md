# EXP-001 — Local Qwen3-4B Baseline

## 1. Objective

Establish the first controlled MiniLab baseline: run the full 8-task suite
against a local Qwen3-4B model and record pass/fail, failure taxonomy, and
trajectories. This is a **controlled baseline for this exact
task suite and configuration**, not a general benchmark score for Qwen3-4B.

## 2. Experimental setup

- Harness: MiniLab at baseline commit (8 tasks, deterministic MiniBank fixture)
- Agent loop: ReAct, injected `model_fn`, per-task `max_steps` budgets
- No retries, no fallback models, no provider switching
- Evaluator: deterministic rule-based checker (no LLM judge)

## 3. Model and inference configuration

- Model file: `Qwen3-4B-Q4_K_M.gguf` (Qwen3-4B, Q4_K_M quant, ~2.33 GiB)
- Inference: llama.cpp `llama-server`, build b11435 / commit 43fe9c642
- Endpoint: `http://127.0.0.1:8081/v1` (external process, started manually)
- Provider: `local`, model identifier: `local:qwen3-4b`
- Temperature: 0.0, context 4096, reasoning disabled at server level
- Hardware: Windows, 16 GB RAM, 11th Gen i5-11300H (4C/8T), CPU-only, no discrete GPU

## 4. Task suite

8 tasks: `waiver-c101`, `waiver-c102`, `waiver-c103` (eligibility verdicts),
`retrieval-bal-a103` (fact retrieval), `calc-total-c101` (trusted computation),
`policy-attr-c102` (rule attribution), `temporal-deposit-a103` (time-window
filtering), `horizon-checking-combined` (7-step multi-customer audit).

## 5. Trial protocol

8 tasks × 2 repetitions (seeds 0 and 1) = **16 trials**. Failed trials were
not rerun. Provider/API faults would have been recorded as `provider_error`
(trial-level, not agent failures); none occurred.

## 6. Results

- Total trials: 16
- Valid trials: 16
- Provider errors: 0 (local infrastructure stable throughout)
- Passed: 4, failed: 12
- **Agent reliability: 4/16 = 25%** (valid trials only)

## 7. Per-task results

| Task | Difficulty | Trials | Passes | Success Rate | Dominant Failure |
|---|---|---|---|---|---|
| calc-total-c101 | easy | 2 | 2 | 100% | — |
| retrieval-bal-a103 | easy | 2 | 2 | 100% | — |
| waiver-c101 | easy | 2 | 0 | 0% | evidence_gap |
| waiver-c102 | medium | 2 | 0 | 0% | policy_misread |
| waiver-c103 | easy | 2 | 0 | 0% | evidence_gap |
| policy-attr-c102 | medium | 2 | 0 | 0% | policy_misread |
| temporal-deposit-a103 | medium | 2 | 0 | 0% | evidence_gap |
| horizon-checking-combined | hard | 2 | 0 | 0% | evidence_gap |

Difficulty breakdown: easy 8 trials / 4 passed (50%); medium 6 / 0 (0%);
hard 2 / 0 (0%).

## 8. Failure taxonomy distribution (valid trials only)

`evidence_gap` 8, `policy_misread` 4, `none` 4. No `tool_misuse`,
`arithmetic_error`, `premature_stop`, or `format_violation`.

## 9. Trajectory-level observations

1. Tool execution mechanics are generally strong: arguments were well formed
   and correct in the analyzed failures.
2. Premature finalization is the major weakness: the agent stops once it
   believes it can answer instead of satisfying the full evidence/tool
   contract.
3. Genuine policy-reasoning defect in `waiver-c102`: retrieved 600 and the 500
   threshold, then reasoned 600 < 500.
4. Retrieved evidence can be ignored at final-answer time: in `waiver-c103`
   the model retrieved balance 300, then claimed the average balance was not
   provided.
5. Several failures are strict evidence-contract failures rather than
   substantive reasoning failures (`waiver-c101`, `temporal-deposit-a103`,
   `horizon-checking-combined` — the last follows the oracle step-for-step
   and fails only on uncited account IDs).
6. Both repetitions were behaviorally identical at temperature 0.
7. Local infrastructure stable: 16/16 valid, 0 provider errors.

## 10. Interpretation

Do not read 25% as a Qwen3-4B capability score. It is the outcome of this
task suite, this evidence contract, and this configuration. The trajectory
record shows a mix of one real reasoning defect, several incomplete
investigations, and several contract-strictness failures — these are
different claims and should not be averaged into one.

## 11. Limitations

- n=16 (2 repetitions); no statistical power, no cross-model comparison.
- Single local model, single temperature, single context size.
- Evidence contract is strict by design; part of the failure mass measures
  citation compliance, not reasoning.
- Results are specific to llama.cpp b11435 + Qwen3-4B-Q4_K_M with reasoning
  disabled; other builds, quants, or thinking-mode settings may differ.

## 12. Reproducibility command

Requires the local llama-server from §3 running on port 8081 first
(external dependency, never started by MiniLab):

```powershell
python -m minilab.runner `
  --tasks all `
  --repeats 2 `
  --seed 0 `
  --model "local:qwen3-4b" `
  --results results/local_qwen3_8task_2x.jsonl `
  --trajectories results/local_qwen3_8task_2x_traj.jsonl
```

## 13. Raw artifacts

- `results/local_qwen3_8task_2x.jsonl` (16 rows, ~8 KB)
- `results/local_qwen3_8task_2x_traj.jsonl` (16 trajectories, ~60 KB)

Both are committed with this baseline and must not be rewritten.
