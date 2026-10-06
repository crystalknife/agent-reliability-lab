# MiniLab Architecture

Pipeline:

```
Task Suite → Agent Harness → MiniBank Environment / Tools → Trajectory Recorder
    → Deterministic Evaluator → Failure Taxonomy → Metrics → Results
                                                      (future: frontend/leaderboard)
```

## Module boundaries

- `env.py` — owns MiniBank state. Loads `data/bank.json` (never mutated),
  answers customer/account/transaction/policy queries. No agency, no tools.
- `tools.py` — the 5 tool schemas (`get_customer`, `get_account`,
  `get_transactions`, `search_policy`, `calculate`) plus `dispatch`, the
  single tool-execution entry point, and the system prompt renderer.
- `tasks.py` — task definitions + ground truth + validation. Each task
  declares its prompt, verdict/answer expectations, required tools, grounded
  numbers, per-task `max_steps` budget, and optional `support` /
  `expected_answer` gates. Schema-validated on import.
- `agent.py` — the ReAct loop. Takes an injected `model_fn`, runs at most
  `max_steps` turns, never raises: model exceptions become `model_error`,
  dispatch faults become `tool_error`. Returns a `Trajectory`.
- `trajectory.py` — canonical schema (`task_id`, `steps`, `final_answer`,
  `termination_reason`) plus provenance (experiment/model/seed/config).
  The evaluator reads canonical fields only; `raw_history` is debug context.
- `evaluation.py` — deterministic rule-based checker, no LLM judge. Verdict
  match, evidence citation, tool/number grounding, and optional
  domain-support checks (currently waiver-only, per-task opt-in).
- `failures.py` — two independent axes: trial status (`valid`,
  `provider_error`, `environment_error`, `evaluator_error`) and the agent
  taxonomy (`tool_misuse`, `evidence_gap`, `policy_misread`,
  `arithmetic_error`, `premature_stop`, `format_violation`). Provider faults
  are never charged to the agent.
- `runner.py` — experiment driver: repeats, seeds, model selection
  (`stub` / `scripted` / provider adapters), config recording, JSONL
  storage. Also holds the `scripted` oracle sequences used as a
  deterministic control.
- `metrics.py` — pure aggregation over results JSONL. Reports total/valid
  trials, provider errors, agent reliability over valid trials only, and
  by-task / by-difficulty breakdowns.
- `models/` — provider adapters (`openai`, `gemini`, `groq`, `openrouter`,
  `omniroute`, `local`). Each exposes `make_*_model` returning a `model_fn`.
  External services/servers stay external; adapters never start them.

## Key invariants

- Per-task `max_steps` is the single interaction-budget authority; no global
  override exists.
- Trajectory files are append-only and never rewritten.
- `results/` holds research artifacts; providers' credentials never enter
  the repo (see `API_KEYS/`, which is git-ignored and unpublished).
- Anything the evaluator needs from a task must be declared on the task;
  domain logic gated by explicit opt-in, never by observation accident.
