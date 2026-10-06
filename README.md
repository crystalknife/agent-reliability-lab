# Mini Agent Reliability Lab

Mini Agent Reliability Lab is an **experimental framework for studying agent
reliability under controlled, repeatable tasks**. It is currently a small
research prototype rather than a mature benchmark.

Deterministic MiniBank fixture + tool loop + trajectory recording +
rule-based evaluation + failure taxonomy + repeated runs + metrics.

## Current Status

- 8-task suite: eligibility verdicts, retrieval, calculation, rule
  attribution, temporal filtering, and a multi-step audit task.
- First controlled baseline recorded: local Qwen3-4B, 8 tasks × 2 reps =
  16 trials, 16 valid, 4 passed (25% agent reliability on this
  configuration — a baseline reading, not a model capability score).
  See `docs/experiments/EXP-001-local-qwen3-baseline.md`.
- Deterministic oracle control passes 8/8. Full test suite green.

## Why this exists

Leaderboard scores don't explain *how* agents fail. MiniLab records full
trajectories, evaluates them with a deterministic checker (no LLM judge),
and classifies failures with a fixed taxonomy — separating agent behavior
from provider/infrastructure faults — so each failure can be traced to an
exact step, tool call, and observation.

## Architecture

```
Task Suite → Agent Harness → MiniBank Environment / Tools → Trajectory Recorder
    → Deterministic Evaluator → Failure Taxonomy → Metrics → Results
```

See `docs/architecture/ARCHITECTURE.md` for module boundaries. A
frontend/leaderboard is future work, not present.

## Capabilities

- 8 deterministic benchmark tasks (`src/minilab/tasks.py`), schema-validated
- ReAct agent loop with injected `model_fn` (`src/minilab/agent.py`)
- 5 MiniBank tools + sandboxed `calculate` (`src/minilab/tools.py`)
- Canonical trajectory schema with provenance (`src/minilab/trajectory.py`)
- Deterministic evaluator: verdict, evidence citation, tool/number
  grounding, optional domain-support checks (`src/minilab/evaluation.py`)
- Trial status vs agent failure taxonomy kept separate (`src/minilab/failures.py`):
  provider errors are never charged to the agent
- Provider adapters: `stub`, `scripted` (deterministic oracle control),
  OpenAI, Gemini, Groq, OpenRouter, OmniRoute, and local llama-server
- Metrics over valid trials only (`src/minilab/metrics.py`)

## Task suite

`waiver-c101/c102/c103` (eligibility), `retrieval-bal-a103`,
`calc-total-c101`, `policy-attr-c102`, `temporal-deposit-a103`,
`horizon-checking-combined`. Each declares its own `max_steps` budget
(oracle invocations + 1 slack); there is no global override.

## Running tests

```powershell
pip install -e .
python -m unittest discover -s tests -v
```

## Running the deterministic oracle baseline

```powershell
python -m minilab.runner --tasks all --repeats 1 --seed 0 --model scripted
python -m minilab.metrics --results results/results.jsonl
```

Expected: 8/8 passed. Any real-model failure against this control is
attributable to the model, not the harness.

## Running the local Qwen3 setup

The local server is an external dependency (llama.cpp `llama-server`,
Qwen3-4B-Q4_K_M, port 8081) and is never started by MiniLab. With it
running:

```powershell
python -m minilab.runner --tasks all --repeats 2 --seed 0 --model "local:qwen3-4b" `
  --results results/<experiment>.jsonl --trajectories results/<experiment>_traj.jsonl
```

## Results

`results/` holds append-only JSONL research artifacts, including the
committed EXP-001 baseline (`local_qwen3_8task_2x*.jsonl`). Result files
are never rewritten. API credentials live in git-ignored `API_KEYS/` and
are never committed.

## Limitations

- Small task suite (8 tasks), 2-repetition baselines: no statistical power.
- Evidence contract is strict by design; part of the failure mass measures
  citation compliance, not reasoning.
- Deterministic evaluation covers only what the rules express; nuanced
  partial credit does not exist.
- temperature=0 does NOT guarantee deterministic LLM behavior; the runner
  records full config for auditability instead.
- `scripted` validates harness plumbing, not capability.

## Roadmap

- More task families (conflict resolution, recovery, format constraints)
- Optional support declarations beyond the waiver domain
- Cross-model comparisons with quota-aware protocols
- Frontend / leaderboard (future)
