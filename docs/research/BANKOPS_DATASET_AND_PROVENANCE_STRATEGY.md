# BankOps dataset, IP and provenance strategy

**Revised 2026-11-17 (Day 2).** Licensing and legal positions are **DECISIONS** for the owner, not advice.

**Labels:** VERIFIED / INFERRED / HYPOTHESIS / UNKNOWN / DECISION.

## 1. Facts that constrain this strategy (VERIFIED)
- **This repository has no `LICENSE` file**, and `pyproject.toml` declares no licence. Code and public tasks therefore have no grant today. Decision D8.
- **External sources** (details in landscape §5):
  - MIT: BizAgentBench, τ-bench, FinQA, ConvFinQA, IdempotencyBench, "Did It Happen?" artifact, AgentCheck.
  - Apache-2.0: BankerToolBench.
  - Unlicense: Silo-Bench.
  - **No licence file:** FinToolBench, FinGAIA, FinanceBench, ToolMaze. **Do not reuse.**
  - **TELLER-Bench: conflicting licences** (Dataverse record CC0 1.0; README CC BY 4.0).
  - Bank-World: environments private.
- **Historical ARL experiment data** (EXP-001 to EXP-004) stays as it is. It is not republished, rescored or folded into BankOps splits.

## 2. Three data boundaries
| Artefact | Public | Private benchmark | Enterprise-private |
|---|---|---|---|
| Methodology, documentation, evaluation tooling | Yes | — | — |
| Environment fixtures (Harbor, Meridian) | Development fixtures | Held-out fixture variants | Customer fixtures and policies |
| Tasks and families | About one third of tasks, with reference solutions | **Held-out variants and unreleased scenarios; protected reference solutions** | Customer workflows |
| Episodes (trajectories, events, ledger snapshots) | Sanitized aggregates and per-episode verdicts only | Full | Customer only |
| Annotations, expert judgements | Aggregates, agreement statistics | Full | Customer only |
| Human completion-time baselines | Per-task t with interval | Raw times, pseudonymised | — |
| Run manifests | Yes | Yes | Customer only |
| Leaderboard | Admitted aggregates | — | Private reports |

## 3. Versioning, lineage and contamination control
- **Suite version:** `suite@MAJOR.MINOR`. Any change to a task's text, fixture, grader or report contract bumps the version, and scores from different versions are never merged.
- **Task record:** `task_id, family_id, parent_task_id, varied_axis, author_role, reviewer_role, llm_assisted: bool, created_at`.
  - Bank-World reports "Tasks were developed with LLM assistance, including from evaluated models" as a limitation (VERIFIED). BankOps records any LLM assistance.
  - **Evaluated models are never used to author tasks** (DECISION, proposed).
- **Held-out commitment:**
  - private tasks carry a canary string;
  - a **hash of each held-out task** is published at freeze;
  - the private split never enters the public repository.
- **Reproducibility without disclosure:** public results are re-runnable exactly. Held-out results are reproduced by a trusted evaluator from manifests and configuration hashes.
- **Manifest extension** (builds on the existing `src/minilab/run_manifest.py`): environment id, version and fixture hash; tool-set hash; fault world; grader version; report-contract version; seed schedule; model profile id (never the credential).

## 4. Human data: annotation and baselines
- **Annotation:** handbook, double annotation, κ, adjudication. Defined in `BANKOPS_FAILURE_TAXONOMY.md` §5.
- **Expert completion times:** protocol in `BANKOPS_LONG_HORIZON_METHODOLOGY.md` §3.
- **Consent and pay** (decision D7): written consent; pseudonymous ids in every export; raw identity data stored separately with restricted access. Retention period (DECISION, proposed: the life of the benchmark version plus 2 years).

## 5. Access control, privacy and security
- **No real customer data and no real institutions:** all fixtures are synthetic, and names are fictional.
- **Private-split storage:** a separate private repository or encrypted store with named access (decision D8).
- **Credentials:** never stored in data, manifests or logs. Only `env:NAME` references (readiness §8, test A9).
- **Public results:** only sanitized aggregates and verdicts. The existing sanitizer patterns (`src/minilab/live/sanitize.py`) are reused.

## 6. The public-tool / private-dataset model, as an analogy only
- **The analogy:** some public evaluation platforms publish tooling and aggregate rankings while protecting the data that makes the evaluation hard to game. Bank-World keeps its environments private "to reduce contamination" (VERIFIED).
- **What it does not show:** the LMArena comparison was **not researched** this session (UNKNOWN). It is not evidence that our arrangement is legally or commercially equivalent to anyone's.
