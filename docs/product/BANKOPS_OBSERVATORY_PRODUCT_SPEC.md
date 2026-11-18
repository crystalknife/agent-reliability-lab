# BankOps Observatory: product specification

**Revised 2026-11-17 (Day 2).** Status: **proposed**; the scope follows `docs/roadmap/BANKOPS_DAY2_READINESS.md` §7.

**Labels:**
- §1 alternatives: **VERIFIED**. §1 product value: **HYPOTHESIS**.
- §2–§4 screens, contracts and rules, and §5 prerequisites: **DECISION** (proposed, pending owner approval).
- Existing routes and components cited: **VERIFIED** in this worktree.

This is an evaluation workbench, not a chat interface. Every screen reads backend state: no mock data, no static sample leaderboard.

## 1. Is it useful beyond a research dashboard? (reality check)
**Verified alternatives:**
- **Public, deterministic, permissively licensed harnesses already exist:** BizAgentBench (MIT, payments workflows), τ-bench (MIT), and IdempotencyBench (MIT, duplicate side effects under retries).
- **Bank-World keeps its environments private.**

**So a product cannot rest on "a banking benchmark exists".** Product value is a set of **hypotheses**, to be tested in `BANKOPS_COMMERCIAL_VALIDATION.md`:
- configurable evaluations under **banking controls plus operational faults** (H6), run against a team's own model and scaffold;
- **private held-out** suites, so results resist contamination;
- **regression testing** of agent releases, with comparable configurations and provenance;
- reports that **model-risk or compliance** reviewers can audit: deterministic checks and manifests, with no LLM-judge authority.

## 2. Navigation and staging
| Section | Route | Week 1 | Later |
|---|---|---|---|
| Overview | `/` (existing) | Keep; link to BankOps | — |
| Experiments and Runs | `/experiments`, `/live` (existing) + `/bankops/runs/:id` | **Minimal run view:** per-episode verdicts, invariant violations, fault world, aggregates with Wilson intervals | Trajectory and event drill-down |
| Comparable results | `/bankops/results` | **Equivalence-class table:** suite@version, environment@version, tool-set hash, fault world, step budget, repeats | Public leaderboard (§4) |
| Run Evaluation (configuration UI) | `/bankops/run` | **Week 2.** Week 1 runs start from the CLI with a validated configuration file | Model, environment, tool toggles, suite, budgets, validation, run |
| Benchmark Suites / Environments / Tools / Model Providers | `/bankops/{suites,environments,tools,models}` | Week 2 (read-only registry views) | — |
| Failure Analysis | `/bankops/failures` | Week 2 | v2 taxonomy views |
| Research and Methodology | `/research` | Links to `docs/research/*` | — |

## 3. Run configuration (one contract for CLI and UI)
- **Model profile:** `{id, kind: api|local, base_url, model_id, credential_ref: "env:NAME" | null, timeout_s, max_retries}`. Model ids are typed in explicitly, with no catalogue; `credential_ref` is shown, a value never is.
- **Selections:**
  - environment `{id, version}`;
  - `tools.enabled[]`;
  - suite `{id, version, task_ids?}`;
  - repeats, seed schedule, step budget, fault worlds.
- **Validation** returns `{ok, errors[], warnings[], config_sha}`. **Incompatible task/tool combinations are rejected with the missing tools listed, never fixed automatically.**
- **The UI toggle is not the control:** the dispatcher enforces the allow-list at runtime (readiness §5.6).

## 4. Comparable results and leaderboard rules
- **Equivalence classes:** rows are grouped by `(suite@version, env@version, toolset_sha, fault_world, max_steps, repeats, report_contract)`. Models are ranked **only within a class**. Cross-class views are side by side and never merged.
- **Columns:**
  - model profile, scaffold@version;
  - valid episodes;
  - safe success (Wilson, or a Kish-adjusted interval when clustered);
  - ledger-integrity breaches;
  - control-violation attempts;
  - misreports;
  - pass^k;
  - latency; tokens and cost when measured;
  - provenance status.
- **Admission:** a manifest is present; the grader version matches; grader validation passed; provider and environment errors are excluded from denominators and shown separately.
- **Public-safe content:** aggregates and verdicts only. No trajectories, prompts or arguments from held-out tasks.

## 5. Prerequisites before external submissions
These must exist before any external submission is accepted (`BANKOPS_PUBLIC_API_AND_SUBMISSIONS.md` §4):
- sandboxed agent execution outside the trusted process;
- credential isolation;
- timeouts and resource limits;
- rate limiting and quotas;
- abuse protection, including canary-leak scanning;
- model identity and configuration capture, verified only when calls pass through our gateway;
- signed result manifests;
- submission provenance;
- separation of public and private runs.

## 6. Non-goals until further notice
User simulator; multi-agent runs; hosted deployment; authentication; uploading custom environments.
