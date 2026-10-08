<div align="center">

[![Agent Reliability Lab](docs/assets/minilab-title-light.png#gh-light-mode-only)](https://agent-reliability-minilab.vercel.app/)
[![Agent Reliability Lab](docs/assets/minilab-title-dark.png#gh-dark-mode-only)](https://agent-reliability-minilab.vercel.app/)

**An experimental framework for measuring AI-agent reliability through controlled tasks, tool use, trajectories, and deterministic evaluation.**

[![CI](https://github.com/crystalknife/agent-reliability-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/crystalknife/agent-reliability-lab/actions/workflows/ci.yml)
[![Live Dashboard](https://img.shields.io/badge/live-observability%20dashboard-000000?logo=vercel)](https://agent-reliability-minilab.vercel.app/)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![License: unspecified](https://img.shields.io/badge/license-not%20yet%20declared-lightgrey)](#license)

[Live Dashboard](https://agent-reliability-minilab.vercel.app/) ·
[Quickstart](#-quickstart) ·
[Experiments](#experiments) ·
[Architecture](#architecture) ·
[Deployment](#deployment) ·
[Contributing](#contributing)

**[OPEN LIVE OBSERVATORY ↗](https://agent-reliability-minilab.vercel.app/)**

</div>

---

> **Status:** Agent Reliability Lab is a **MiniLab — an experimental prototype**, not a mature or universal benchmark. It is intentionally controlled and lightweight. The long-term direction is a domain-specific agent reliability benchmark, and possibly a public leaderboard. Current results are experiments under a specific methodology, **not** broad claims about model intelligence or general agent reliability.

## Overview

Most leaderboards report whether an agent produced the right final answer. That signal is useful but shallow — it cannot tell you *how* an agent failed, which tool call went wrong, or whether it ignored evidence it had already retrieved. Agent Reliability Lab is built to make those questions measurable.

It measures agent reliability by running agents through small, controlled tasks, recording full tool-use trajectories step by step, and evaluating the result with a **deterministic, rule-based checker** (no LLM judge). Every trial is classified against a fixed failure taxonomy, and failures are kept separate from provider/infrastructure faults so that an agent is never charged for a broken API.

The core properties of the current MVP:

- **Controlled environments.** Tasks run against a deterministic, sandboxed fixture with a fixed set of tools.
- **Recorded trajectories.** Every model action, tool call, argument, observation, and termination reason is preserved.
- **Deterministic evaluation.** Verdict, evidence citation, and numeric/tool grounding are checked by rules, not a model.
- **Failure classification.** A small, fixed taxonomy describes *agent-level* failures.
- **Repeated trials.** Experiments run the same suite multiple times to produce measurable, reproducible results.
- **Preserved artifacts.** Results and trajectories are append-only research artifacts.

## Capabilities

<table>
<tr><td>

### Controlled Agent Evaluation
- Deterministic task environments
- Explicit task specifications
- Required-tool constraints
- Grounded-answer checks
- Per-task step budgets

</td><td>

### Trajectory-Level Observability
- Model actions per step
- Tool calls and arguments
- Observations returned
- Termination reasons
- Provenance metadata

</td></tr>
<tr><td>

### Deterministic Evaluation
- Expected answers / verdicts
- Required evidence citations
- Grounded numeric evidence
- Required tools
- Fixed failure taxonomy

</td><td>

### Experiment Analysis
- Trial validity tracking
- Provider vs agent failure separation
- Success / failure rates
- Failure-category breakdown
- Per-task and per-difficulty analysis

</td></tr>
</table>

### Local and API Models

Model selection is a single `--model` string. The harness ships with built-in local controls and adapters for external providers:

- `stub` and `scripted` — fully local, deterministic controls (no network).
- `local:<model-id>` — an external local `llama-server` (OpenAI-compatible).
- `omniroute` — a local OmniRoute server.
- `gemini`, `groq`, `openrouter` — hosted API providers.
- Any other string is treated as an OpenAI model name.

Adapter SDKs are optional extras and imported lazily — the core harness runs on the standard library alone. See [Model Providers](#model-providers).

## Live Observability Dashboard

The recorded experiment data has a read-only observability frontend, deployed at:

**<https://agent-reliability-minilab.vercel.app/>**

It lets you explore what the experiments produced:

It lets you explore what the experiments produced:

- Experiment overview and provenance
- Per-task performance
- Trial inspection
- Step-by-step trajectory inspection
- Tool calls and their observations
- Failure categories
- Filtering and deep links

> The frontend **does not execute the benchmark**. It is a static observability layer over recorded artifacts. Python remains the authoritative experiment and evaluation layer.

## Current Experiment

The first controlled baseline is **EXP-001** — the full 8-task suite run against a local Qwen3-4B model.

| Field | Value |
|---|---|
| Experiment | EXP-001 |
| Model | local Qwen3-4B (`local:qwen3-4b`) |
| Task suite | 8 tasks |
| Trials | 16 (8 tasks × 2 seeds) |
| Valid trials | 16 |
| Provider errors | 0 |
| Passed | 4 |
| Failed | 12 |
| Agent reliability | **25%** (valid trials only) |

**Failure breakdown** (valid trials): `evidence_gap` 8, `policy_misread` 4, `none` (pass) 4.

> **This is a baseline experiment under the current MiniLab methodology — not a general claim about Qwen3-4B.** It is the outcome of this specific task suite, evidence contract, and inference configuration, on CPU-only hardware.

Read the full write-up: [`docs/experiments/EXP-001-local-qwen3-baseline.md`](docs/experiments/EXP-001-local-qwen3-baseline.md).

## Architecture

```text
Task Suite
    ↓
Agent Harness
    ↓
Environment + Tools
    ↓
Trajectory Recorder
    ↓
Deterministic Evaluator
    ↓
Failure Taxonomy
    ↓
Results + Metrics
    ↓
Observability Frontend
```

| Stage | Responsibility |
|---|---|
| **Task Suite** | Declares prompts, expected verdicts/answers, required tools, grounded numbers, and per-task step budgets. Schema-validated on import. |
| **Agent Harness** | ReAct loop with an injected `model_fn`. Never raises: model exceptions become `model_error`, tool faults become `tool_error`. |
| **Environment + Tools** | Deterministic MiniBank state answering customer/account/transaction/policy queries, plus the 5 tool schemas and a sandboxed `calculate`. |
| **Trajectory Recorder** | Canonical step schema (actions, tool calls, observations, termination reason) plus experiment/model/seed provenance. |
| **Deterministic Evaluator** | Rule-based verdict match, evidence citation, and tool/number grounding. No LLM judge. |
| **Failure Taxonomy** | Splits trial status (valid / provider_error / …) from the agent-level failure category. |
| **Results + Metrics** | Append-only JSONL results and pure aggregation over valid trials. |
| **Observability Frontend** | Static read-only UI over the recorded artifacts. |

See [`docs/architecture/ARCHITECTURE.md`](docs/architecture/ARCHITECTURE.md) for module boundaries and invariants.

## Project Structure

```text
agent-reliability-lab/
├── src/minilab/        # experiment engine (tasks, agent loop, env, evaluator, metrics)
│   └── models/         # provider + local adapters
├── tests/              # Python test suite
├── data/               # deterministic MiniBank fixture
├── results/            # append-only experiment artifacts (JSONL)
├── docs/               # architecture + experiment write-ups
├── frontend/           # React + TypeScript observability UI (Vercel)
├── scripts/            # data export helpers
├── .github/workflows/  # CI
└── pyproject.toml
```

Key modules:

- `src/minilab/tasks.py` — task definitions + ground truth + validation.
- `src/minilab/agent.py` — the ReAct agent loop.
- `src/minilab/env.py` — MiniBank state (loads `data/bank.json`, never mutated).
- `src/minilab/tools.py` — the 5 tool schemas + dispatch.
- `src/minilab/evaluation.py` — deterministic rule-based checker.
- `src/minilab/failures.py` — trial status + failure taxonomy.
- `src/minilab/runner.py` — experiment driver (repeats, seeds, model selection, JSONL).
- `src/minilab/metrics.py` — aggregation over results JSONL.

## MiniBank Environment

The controlled environment is **MiniBank** — a fictional banking domain built for repeatable agent evaluation. It exposes five tools:

| Tool | Purpose |
|---|---|
| `get_customer` | Look up a customer record |
| `get_account` | Look up an account |
| `get_transactions` | Retrieve transactions |
| `search_policy` | Retrieve policy text |
| `calculate` | Sandboxed arithmetic |

The environment is deterministic and intentionally limited so results are comparable across runs.

> MiniBank is a benchmark environment for experimentation — **not** a production banking system.

## Failure Taxonomy

Agent-level failures are classified into exactly these categories:

- `tool_misuse`
- `evidence_gap`
- `policy_misread`
- `arithmetic_error`
- `premature_stop`
- `format_violation`

Trial status is tracked on a separate axis (`valid`, `provider_error`, `environment_error`, `evaluator_error`), so an infrastructure fault is never recorded as an agent failure. The taxonomy is intentionally kept small during the current experimental phase.

## Experiments

An experiment is a repeated, recorded run of a task suite:

- **Repeated runs** with explicit **seeds**.
- **Model/provider metadata** and inference config recorded per run.
- **Full trajectory recording** for every trial.
- **Append-only JSONL artifacts** that are never rewritten.
- **Experiment documentation** alongside the raw artifacts.
- **Separation of valid trials from provider/infrastructure failures.**

Reproducibility metadata (model, provider, seed, temperature, run identifiers) is preserved with every trial, so a result can be traced back to the exact configuration that produced it.

## 🚀 Quickstart

Requires **Python 3.10+**. The core harness uses only the standard library.

```powershell
# 1. Clone and install
git clone https://github.com/crystalknife/agent-reliability-lab.git
cd agent-reliability-lab
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

Run the test suite:

```powershell
python -m unittest discover -s tests -v
```

Run the deterministic oracle control (validates the harness — expected **8/8 passed**):

```powershell
python -m minilab.runner --tasks all --repeats 1 --seed 0 --model scripted
python -m minilab.metrics --results results/results.jsonl
```

Run a first experiment against the built-in stub and aggregate it:

```powershell
python -m minilab.runner --tasks all --repeats 2 --seed 0 --model stub `
  --results results/my_experiment.jsonl `
  --trajectories results/my_experiment_traj.jsonl
python -m minilab.metrics --results results/my_experiment.jsonl
```

Run the observability frontend locally (optional — static over recorded data):

```powershell
cd frontend
npm ci
npm run data   # regenerate static experiment data from results/
npm run dev
```

## Model Providers

Model selection is driven entirely by the `--model` string. There are three execution shapes:

- **Built-in local controls** (`stub`, `scripted`): deterministic, no network. `scripted` replays the oracle tool sequence and is used as the control that validates the harness plumbing.
- **External local servers** (`local`, `omniroute`): OpenAI-compatible servers you run yourself (e.g. a local `llama-server` on port 8081). The harness **never starts them**.
- **Hosted API providers** (`gemini`, `groq`, `openrouter`, OpenAI): require an API key in the environment and the matching optional SDK (e.g. `pip install -e ".[openai,gemini]"`).

API-backed and local execution differ only in where inference happens; everything downstream (trajectory recording, evaluation, metrics) is identical. **Provider and infrastructure errors are classified separately from agent failures** — a rate-limit or a dead server is never counted against the agent.

> No credentials are stored in the repository. Provider API keys are read from the environment (and local, git-ignored key files), never committed. Free API access is not implied — you supply your own keys and quota.

## Reproducibility

The lab separates what is deterministic from what is not:

- **Deterministic by design:** the MiniBank environment, the tools, and the rule-based evaluator.
- **Explicitly recorded:** seeds, model/provider identifiers, temperature, prompt provenance where available, run identifiers, and full trajectories.
- **Append-only:** experiment artifacts are written once and never rewritten.

> The environment and evaluator are deterministic by design; **model inference may still vary** depending on the provider/runtime. `temperature=0` does not guarantee identical output across builds or providers, so full configuration is recorded for auditability rather than assumed.

## Development

- CI runs on every pull request targeting `main` and on pushes to `main`.
- Two required checks: **Python tests (204)** and **Frontend tests, typecheck, build**.
- `main` is protected: changes land through pull requests with these checks passing.
- The frontend job (`frontend/`) runs `npm ci`, `npm test`, `npm run typecheck`, and `npm run build`.

See [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

## Deployment

- The frontend is deployed on **Vercel** with GitHub-connected automatic deployments: updates to `main` redeploy the live site.
- Vercel root directory is `frontend/`; **Vite** builds the static output to `dist/`.
- An **SPA fallback** rewrite serves `index.html` for client-side routes so deep links (e.g. `/experiments/EXP-001`) resolve correctly.
- The Python experiment engine is **separate** — it runs locally / from the repository and is never hosted.

Live: **<https://agent-reliability-minilab.vercel.app/>**

## Roadmap

**Done**

- [x] Controlled MiniBank environment
- [x] Trajectory recording
- [x] Deterministic evaluator
- [x] Failure taxonomy
- [x] Repeated experiments
- [x] Local Qwen3 baseline (EXP-001)
- [x] Observability frontend
- [x] CI validation

**Next**

- [ ] Larger task suites
- [ ] Controlled fault injection
- [ ] Recovery / reliability experiments
- [ ] Stronger statistical reporting
- [ ] Token / cost / latency measurements
- [ ] Broader model and provider comparisons
- [ ] Domain-specific benchmark expansion
- [ ] Public leaderboard

## Contributing

1. Fork or clone the repository.
2. Create a feature branch off `main`.
3. Make focused changes — keep the benchmark methodology and task definitions separate from unrelated work.
4. Run the tests locally:
   ```powershell
   python -m unittest discover -s tests -v
   ```
5. Open a pull request against `main`.

Because `main` is protected, all changes land through pull requests with the required CI checks passing. No `CONTRIBUTING.md` exists yet; this section is the current guide.

## Maintainer

Maintained by **Balasubrahmanya A G** ([@crystalknife](https://github.com/crystalknife)).

## License

No license has been explicitly declared yet. The repository is public, but licensing terms have not been established — treat reuse rights as undefined until a license is added.

> Note: the frontend vendors/adapts a small component from Animate UI, whose license is MIT + Commons Clause. This is relevant to (and should be reconciled with) any future license decision.

## Acknowledgements

The frontend's motion and presentation layer draws on these projects:

- **Animate UI** — vendored/adapted collapsible component (MIT + Commons Clause).
- **Lenis** — smooth-scroll feel layer (respects reduced-motion; not mounted on coarse-pointer devices).

---

<div align="center">
<sub>Agent Reliability Lab is an experimental research prototype. Its results describe specific configurations, not general model capability.</sub>
</div>
