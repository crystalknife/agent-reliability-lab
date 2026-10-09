# EXP-003 — Controlled Framework Replication (Protocol; not yet run)

Status: **APPROVED PROTOCOL. EXPERIMENT NOT EXECUTED.** No EXP-003
artifacts exist. Do not treat anything below as results.

## 1. Research question and hypothesis

Question: once model-facing tool schemas are equated, does the observed
pass-rate gap between the custom MiniLab harness and the LangChain arm
persist at higher repeat counts?

Working expectation (not a claim): EXP-002B matched EXP-001 at 25% under
schema parity; EXP-003 tests whether that match replicates at 40 trials
per arm. Any outcome — match, split, or inversion — is informative. This
protocol does not hypothesize framework superiority in either direction.

## 2. Scope and explicit non-claims

- This experiment compares two orchestration loops over identical tasks,
  environment, tools, evaluator, model, and temperature.
- It does **not** establish general framework superiority, regardless of
  outcome. n=40 per arm is still pilot-scale.
- It does **not** claim full orchestration parity: parallel tool-call
  handling, malformed-argument paths, unknown-tool paths, and budget
  accounting differ by design (see §5 and §7).
- It does **not** guarantee trial independence in any statistical sense
  beyond separate model invocations with recorded seeds/UUIDs.

## 3. Arms and exact configuration

- Arm A: custom MiniLab harness (`--framework minilab`, strategy `normal`)
- Arm B: LangChain with `--strategy schema_parity`
- Model: `local:qwen3-4b` (external llama-server, port 8081)
- Task suite: all eight MiniBank tasks, per-task `max_steps` budgets unchanged
- Temperature: 0
- Repeats: 5 per task per arm (seeds 0–4), 40 trials per arm, 80 total
- Evaluator, MiniBank fixture, tool semantics: unchanged

## 4. Exact commands and artifact paths

Run from the repository root, Arm A first, then Arm B:

```powershell
python -m minilab.runner --framework minilab --model "local:qwen3-4b" `
  --tasks all --repeats 5 --seed 0 --temperature 0 `
  --results results/exp003_custom_8task_5x.jsonl `
  --trajectories results/exp003_custom_8task_5x_traj.jsonl
```

```powershell
python -m minilab.runner --framework langchain --strategy schema_parity `
  --model "local:qwen3-4b" --tasks all --repeats 5 --seed 0 --temperature 0 `
  --results results/exp003_langchain_schema_parity_8task_5x.jsonl `
  --trajectories results/exp003_langchain_schema_parity_8task_5x_traj.jsonl
```

All four paths must be verified absent before execution (filenames reserved
by `docs/experiments/EXP-002-langchain-framework-pilot.md` §10). Result
files open in **append** mode: never relaunch against existing files and
never append a new run to existing artifacts. Interrupted-arm recovery
follows §8 exclusively.

## 5. Controlled variables and known confounders

Held constant: system prompt (single `build_system_prompt()` source),
model-facing tool schemas (parity rendering asserted by unit tests),
task prompts, per-task budgets, environment, evaluator, model, temperature,
seed protocol, trial-validity rules.
Known open differences (documented, not normalized): parallel multi-call
batches (LangChain executes whole AIMessage batches; harness takes the
first call only); malformed-argument path (provider_error vs error
observation); unknown-tool path (tool_error vs error observation);
recursion-unit vs model-call budget accounting (calibrated, +2 boundary).

## 6. Seed semantics

`seed_used = seed + rep` (seeds 0–4), recorded per row/trajectory. Seeds
are **not** passed to local inference (neither arm sends them; temperature
is the only sampling parameter transmitted). These labels are metadata for
grouping repeated trials: they identify repetitions for analysis, do not
control the sampler, and do not establish independent samples or seed
effects. `run_id` is a fresh UUID4 per trial (join key); `human_run_id`
(`task-s{seed}-r{rep}`) collides across experiments by design — never
join on it.

## 7. Parallel tool-call limitation

Preserved as-is per approval: no single-call enforcement. If batches
occur, they are recorded in `raw_history` (all calls, all observations)
and classified normally. Report batch frequency per arm as in EXP-002.

## 8. Trial-validity and provider-error policy

Attempt all 80 planned trials. Do **not** silently retry or replace
provider-failed trials. Preserve every fault in the artifacts; report
provider errors separately from agent reliability (valid-trials-only
denominator, per evaluator methodology). Stop conditions: server crash,
model unavailable, or artifact integrity compromised (duplicate run_ids,
unparseable lines, mismatched result/trajectory pairings).

Interrupted-arm recovery (the single procedure referenced by §4 and §12):
never overwrite existing artifacts and never append a continuation to them.
Preserve partial result and trajectory files unchanged, then inventory and
validate completed trials, provider failures, duplicate IDs, malformed
JSONL lines, and result-to-trajectory correspondence. Any continuation
uses new, uniquely named artifact files. Final analysis may combine only
validated, non-duplicate trials from the original attempt and the
continuation, with provenance retained and the interruption disclosed. Do
not rerun completed trials merely to replace provider failures. If safe
reconciliation is not possible, stop and report the experiment as
incomplete rather than silently repairing or discarding data. The planned
count stays 40 trials per arm; combined validated totals below 40 are
reported as-is with the shortfall disclosed.

Continuation is permitted only where the remaining planned task/repeat
slots are expressible with the approved §4 flags without rerunning an
already-completed (task, repeat) slot — in practice, whole missing tasks
via `--tasks` with the same repeats/seed, recorded as a documented and
reviewed continuation command under new filenames. Duplicates across files
are identified by (task_id, seed) and always resolved in favor of the
original attempt. Partial-task gaps that cannot be addressed without
rerunning completed trials are left as reported shortfall. If targeted
continuation is not safely expressible, stop and report the arm as
incomplete; do not improvise commands or silently change the protocol.

## 9. Primary metric and secondary metrics

- Primary: agent reliability = passed ÷ valid trials, per arm.
- Secondary: per-task pass rates, per-difficulty rates, failure-taxonomy
  mix, termination distribution, multi-call batch frequency,
  per-repeat-index pass rates using recorded seed labels (ad-hoc;
  `metrics.py` aggregates by task/difficulty only).
- Per-arm accounting (required table): planned trials, trials
  completed/attempted, valid trials, provider/infrastructure errors, passed
  trials, pass rate among valid trials. If validity rates differ materially
  between arms, discuss the difference as a limitation (no numeric
  threshold is set; report actual counts and percentages and explain what
  was observed).

## 10. Statistical reporting and uncertainty

Report Wilson 95% intervals alongside observed rates (approximately
35.2%–64.8%, i.e. roughly ±15 percentage points, at a 50% pass rate with
40 valid trials per arm). No significance language, no superiority claims,
no causal attribution beyond the pre-registered confounder analysis.
Compare against EXP-001 (25%, n=16) and EXP-002B (25%, n=16) as context,
not as formal baselines.

## 11. Pre-run checklist

1. `git status` clean on `main`; HEAD recorded.
2. Server health endpoint responds.
3. Model endpoint confirms the intended Qwen3-4B model is loaded; note build/commit.
4. A minimal inference smoke test succeeds through the configured MiniLab
   model adapter (pre-flight check only; never counted as an EXP-003 trial).
5. All four artifact paths verified absent.
6. `python -m unittest discover -s tests` green.
7. Approvals for any deviation from this document recorded.

## 12. Execution and stop conditions

Run Arm A to completion, validate (§13), then Arm B. Stop on: server
failure, model swap/unavailability, artifact corruption, or any prompt to
deviate from §4 commands. Interrupted arms follow the §8 recovery
procedure exclusively. Never edit code, tasks, or evaluator mid-run to
"fix" results.

## 13. Post-run artifact validation

Uninterrupted runs: 40 JSON rows per file, valid JSON, 40 unique run_ids,
result↔trajectory ID correspondence, uniform metadata
(framework/strategy/model/temperature), all 8 tasks × repeat indices 0–4,
no duplicates. Any violation fails the file.

Interrupted runs (see §8): validate each partial file on its own terms
(valid JSON, unique run_ids within the file, result↔trajectory
correspondence, uniform metadata) and then validate the reconciled
dataset the same way across files, rejecting any duplicate trial. A
partial file containing fewer than 40 rows is not rejected for that
reason alone; shortfall against the planned 40 is reported, with
provenance, as part of the dataset. Preserve the partial artifacts
unchanged throughout.

## 14. Analysis checklist

Headline rates + Wilson CIs; per-arm accounting table (§9); per-task and
per-difficulty tables; failure mixes; termination mix; batch frequency;
per-repeat-index (recorded seed labels) ad-hoc rates; passing-set
comparison vs EXP-001/EXP-002B; trajectory spot-checks of divergent tasks.

## 15. Documentation and frontend publication steps

1. Write `docs/experiments/EXP-003-controlled-replication.md` (results +
   interpretation under this protocol's language rules).
2. Register the two arms in `frontend/scripts/export-data.mjs` (explicit
   entries only), regenerate `npm run data`, verify EXP-001 bytes unchanged.
3. Extend the pilot report page or add an EXP-003 section; validate
   tests/typecheck/build/browser as per repo practice.
4. Commit + PR through the normal protected-main workflow.

## 16. Handoff

To continue without this conversation you need only: this document, the
EXP-002 pilot doc (§1–§9 for confounder context), and a clean checkout at
the HEAD recorded in the run log. Open questions: none — all protocol
decisions (parallel calls preserved; exact commands/filenames; no silent
retries; ad-hoc repeat-index reporting) were approved before writing. If the
server, model build, or file layout differs from §3–§4, stop and
re-audit; do not adapt commands silently.
