# EXP-002 — LangChain Framework Pilot

## 1. Objective

EXP-002 investigated LangChain as an agent-framework arm over the same
MiniBank environment, task suite, deterministic evaluator, local Qwen3-4B
model, and temperature used in EXP-001:

    SAME MODEL — local:qwen3-4b, temperature 0
    SAME TASKS — 8-task MiniBank suite
    SAME ENVIRONMENT — deterministic MiniBank fixture
    SAME TOOLS — get_customer, get_account, get_transactions, search_policy, calculate
    SAME EVALUATOR — deterministic rule-based checker, fixed failure taxonomy
    DIFFERENT AGENT FRAMEWORK — LangChain tool-calling agent vs custom harness

This was initially intended as a framework comparison, but subsequent
forensic controls revealed model-facing adapter confounders. This document
records all five arms and what the controls showed. It must not be read as
evidence that LangChain improves agent reliability.

## 2. Experimental setup

- Model: `local:qwen3-4b` (Qwen3-4B-Q4_K_M via external llama-server, port 8081)
- Temperature: 0, seeds 0 and 1 (8 tasks × 2 repeats = 16 trials per arm)
- Deterministic MiniBank fixture and deterministic evaluator (unchanged)
- LangChain: version 1.4.3 (langchain-core 1.6.7, langchain-openai 1.6.7,
  langgraph 1.2.14), strategy `langchain.agents.create_agent`, no memory,
  no retries, no fallbacks, no provider switching
- Framework identity recorded per results row (`framework`, `framework_version`,
  `agent_strategy`, `strategy`); trajectory files stay in the canonical schema

## 3. Results

| Arm | Configuration | Valid | Passed | Failed | Pass Rate |
|---|---|---:|---:|---:|---:|
| EXP-001 | Custom harness | 16 | 4 | 12 | 25% |
| EXP-002 | Normal LangChain | 16 | 8 | 8 | 50% |
| EXP-002A | Chatter stripped | 16 | 8 | 8 | 50% |
| EXP-002B | Schema parity | 16 | 4 | 12 | 25% |
| EXP-002C | Schema + chatter parity | 16 | 4 | 12 | 25% |

Do NOT present the 50% result as a framework improvement. All arms are
n=16 pilots; no significance claims are supported.

## 4. Per-task findings

Passing sets (2 trials per task):

- EXP-001: `retrieval-bal-a103`, `calc-total-c101`
- EXP-002: `retrieval-bal-a103`, `calc-total-c101`, `waiver-c102`, `waiver-c103`
- EXP-002A: `retrieval-bal-a103`, `calc-total-c101`, `waiver-c102`, `waiver-c103`
- EXP-002B: `retrieval-bal-a103`, `calc-total-c101`
- EXP-002C: `retrieval-bal-a103`, `calc-total-c101`

Failure mixes (valid trials): EXP-001 {evidence_gap: 8, policy_misread: 4};
EXP-002 {evidence_gap: 4, policy_misread: 4}; EXP-002A {evidence_gap: 4,
policy_misread: 4}; EXP-002B {evidence_gap: 6, policy_misread: 6};
EXP-002C {evidence_gap: 6, policy_misread: 6} (`none`/pass makes up each
remainder to 16).

## 5. Forensic audit (summary)

- **Prompt parity:** both arms build the identical system prompt from
  `build_system_prompt()`; no injected messages. Equivalent.
- **Schema differences:** LangChain's StructuredTool conversion decorated
  optional parameters (`anyOf`/`null` + `default: null`) and dropped
  `calculate.values.items: {type: number}`. Model-facing difference.
- **Chatter difference:** LangChain retains assistant text alongside tool
  calls in history; the custom harness drops it. Recorded but inert here:
  EXP-002C contained zero chattery messages, so its strip step was a no-op.
- **Parallel-call difference:** LangChain executes all tool calls in one
  AIMessage as a batch (observed: horizon emitted all 7 calls at once, both
  seeds, arms A/B/C); the harness executes exactly one. ~3.6–3.7% of
  LangChain responses; every observed batch failed. MEDIUM protocol
  confounder, no pass attribution.
- **Termination/step behavior:** recursion cap calibrated to the same
  model-call budget (2 units per iteration + 2 for inclusive finals).
- **Malformed-argument path:** harness → model_error/provider_error;
  LangChain → error observation, loop continues. Divergent by design.
- **Unknown-tool path:** harness → tool_error/tool_misuse; LangChain →
  error observation, loop continues. Divergent by design.

## 6. Schema-parity finding (primary)

Normal LangChain: 50%. Schema parity: 25%. Schema + chatter: 25% (same
passing tasks, same failure mix as schema parity alone).

Careful wording: the controlled results indicate that model-facing schema
differences were a material confound in the unadjusted LangChain result.
Definitive causality is not claimed at n=16.

## 7. Parallel tool-call finding

- Custom harness: one tool call per model response (extras discarded).
- LangChain: N calls per AIMessage, executed as a batch.
- Observed only on horizon-checking-combined in every LangChain arm
  (2 responses/arm, ~3.7% of responses, identical 7-call batch).
- All observed multi-call responses failed, in all arms including normal.
- Remains a MEDIUM protocol confounder; no pass in any arm involved a batch.

## 8. Limitations

- n=16 per arm: no statistical power, no significance claims.
- Parallel-call protocol differs (see §7).
- Malformed-argument handling differs (§5).
- Unknown-tool handling differs (§5).
- Budget accounting differs (recursion units vs model calls; calibrated, not identical).
- The EXP-002C chatter control was not exercised (no chattery messages occurred).

## 9. Conclusion

EXP-002 does not establish that LangChain improves agent reliability. Under
schema-parity controls, the observed LangChain pass rate matched the custom
harness at 25%. The unadjusted 50% LangChain result is retained as an
observational arm, not as evidence of framework superiority.

## 10. Next experiment: EXP-003 (not yet run)

Controlled framework replication:

- Arm A: custom harness
- Arm B: LangChain + schema parity
- 8 tasks × 5 repeats = 40 trials per arm

Raw artifacts:

- `results/exp003_custom_8task_5x.jsonl` (+ `_traj.jsonl`)
- `results/exp003_langchain_schema_parity_8task_5x.jsonl` (+ `_traj.jsonl`)

Filenames are reserved by this document; the files do not exist yet.
