# Banking and financial agent benchmark landscape (validated)

**Revised 2026-11-17 (Day 2).** This document replaces the Day 1 version: its numbers came from LLM-assisted extraction and are superseded. The Day 1 copy is backed up outside the repository, with SHA-256 sums.

## How the evidence was gathered
- **Retrieval:**
  - **Full text** was downloaded for every paper in Matrices A and B (§1–§2) and in §3, except TELLER-Bench. Formats: arXiv HTML (LaTeXML), arXiv PDFs and the Research Square PDF.
  - It was converted to plain text deterministically: HTML parsing, or `pdftotext`.
  - Firecrawl web search was used only to **discover** sources, never as evidence.
- **Other primary sources:**
  - TELLER-Bench: its OpenReview abstract page, plus its Dataverse record and released files (`README.md`, `eval.py`, sample and data files).
  - IdempotencyBench: its GitHub repository metadata only.
  - Licences: arXiv abstract pages, the GitHub REST API, the Hugging Face API and the Dataverse API.
- **Checking:** each claim was checked by **regular-expression search for the exact sentence** in those texts. **No LLM-assisted extraction was used for any claim in this document.**
- **Evidence labels:**
  - **V** VERIFIED: the sentence or code was found in the primary source this session.
  - **I** INFERRED: a reasonable reading of verified evidence.
  - **U** UNKNOWN: not established.
  - **V-absent:** a whole-text keyword search found **no** match. Synonyms could be missed, so absence is strong evidence, not proof.
- **Not retrievable:** the TELLER-Bench paper PDF (OpenReview returned HTTP 403). Only its OpenReview abstract and the released dataset and evaluator were inspected.

## 1. Matrix A: scope, environment and grading
| Benchmark (source) | Domain and intended use | Tasks | Stateful? | Mutates financial state? | Authorisation or policy evaluated | Final state graded independently of the agent? | Statistics | Availability and licence |
|---|---|---|---|---|---|---|---|---|
| **Bank-World**, Rupawalla ([2610.39733](https://arxiv.org/abs/2610.39733), 2026-10-29) | Banking workflows "across two simulated institutions" **V** | 50 (25 single-turn, 25 multi-turn) **V**. Categories: credit analysis and funding, payments and cash reconciliation, loan payoff and servicing, collateral and legal rights, compliance and information controls, trust and asset allocation **V** (per-category counts **U**) | Yes: simulator records restored to "captured initial states" **V** | Yes: postings and payments **V** | Yes: approvals. Observed failure: "execute payments while approval is pending" **V** | Deterministic checks plus **LLM-assessed rubric criteria with partial credit** ("weighted fraction of rubric criteria") **V** | 8 attempts per model per task; 1,600 attempts; pass^2/4/8; "95% task-cluster bootstrap intervals"; cross-grader κ 0.863/0.884 **V** | "We retain the task environments, executable graders, and full trajectories privately" **V**. Paper CC BY 4.0 **V** |
| **BizAgentBench**, Zhao et al. ([2610.20424](https://arxiv.org/abs/2610.20424), v1 2026-10-14) | Long-horizon business workflows "from a leading global payments company" **V** | 50 tasks, 789 documents, 601 tools **V** | Yes: "Each run uses an independent copy of the database" **V** | Yes (I). A "temporary reserve breach" must fail the process check **V** | Process and protection requirements; the user simulator "responds to approval requests" **V** | **Deterministic** "checks over business states and execution histories, without using an LLM judge" **V** | pass^k over k-run subsets; three runs per task for most models, one run for two **V** | GitHub `ant-intl/BizAgentBench` **MIT**; Hugging Face dataset **MIT** **V**. Paper CC BY 4.0 **V** |
| **Northbridge**, in "The Harness Is the Model", Huyut ([2611.06116](https://arxiv.org/abs/2611.06116), 2026-11-04) | Banking conduct-rule compliance across agent harnesses **V** | 156 pre-registered cases; 11 rule families paraphrasing EU consumer-credit, payments, … rules **V** | Simulated retail bank **V** | Yes: "thirteen state-changing tools and six lookups" **V** | Conduct rules; violations and over-refusals (I) | Scored from executed episodes (I) | 3,120 two-turn episodes; Cochran's Q; exact McNemar with Holm; logistic GEE clustered on case; Wilson 95% **V** | Code at `mhuyut/harness-is-the-model`; licence NOASSERTION (**U**). Paper: arXiv non-exclusive **V** |
| **TELLER-Bench** ([OpenReview poy05Mbbx2](https://openreview.net/forum?id=poy05Mbbx2); dataset [doi:10.7910/DVN/5RTT64](https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/5RTT64), 2026-05-06) | Banking SOP workflows; the dataset README calls itself "SOP-Bench" **V** | 1,033: loan 214, account 214, credit cards 221, deposit 200, invest 184 **V** | Paper **U**. **The released evaluator has no environment** **V** | Not in the released evaluator **V** | The SOP-mandated API order is scored as `edge_f1` **V** | **No.** `eval.py` scores the *predicted* API names (`node_f1`), adjacency (`edge_f1`) and *agent-reported* output fields (`exec_acc`) **V** | 14 LLMs; the best has 38% execution accuracy (abstract) **V**; methods **U** | **Conflicting licence:** the Dataverse record says CC0 1.0, the README says CC BY 4.0 **V** |
| **τ-bench**, Yao et al. ([2406.12045](https://arxiv.org/abs/2406.12045), 2024) | Retail and airline customer service with a simulated user **V** | U | Yes **V** | Domain records (bookings, orders) (I) | Domain policies **V** | **Final database state compared with the annotated goal state** **V** | Introduced **pass^k** **V** | Code `sierra-research/tau-bench` **MIT** **V** |
| **FinToolBench**, Lu et al. ([2603.08262](https://arxiv.org/abs/2603.08262)) | Financial tool use (market and economic data) **V** | 760 tools; 295 questions (166 single-tool, 129 multi-tool) **V** | Live third-party APIs (I) | **No.** The agent must "never escalate to execution without explicit authorization" **V** | Intent restraint **V** | LLM judge (GPT-5.1), "averaged across three repeats" **V** | Repeated judging only | Repository has **no licence file** **V**. Paper CC BY-NC-SA 4.0 **V** |
| **BankerToolBench**, Lau et al. ([2604.11304](https://arxiv.org/abs/2604.11304)) | Investment-banking deliverables **V** | U | Files and tools (I) | No ledger (I) | — | Agent-as-a-judge "Gandalf" **V** | Pass@1 and Pass@3 **V**. **Human times: "an average of 5 hours and up to 21 hours"**; 502 bankers; 172 task creators (over 5,700 h) **V** | Apache-2.0 (benchmark and grader repositories) **V** |
| **FinGAIA** ([2507.17186](https://arxiv.org/abs/2507.17186)) | Financial agent tasks in 7 sub-domains, including banking **V** | 407 **V** | U | U | U | Accuracy. Best 48.9%; more than 35 points below experts **V** | — | Repository has **no licence file** **V** |
| **Finance Agent Benchmark** ([2508.00828](https://arxiv.org/abs/2508.00828)) | Financial research on SEC filings **V** | 537 expert-authored questions **V** | No (I) | No (I) | — | Rubric-based LLM evaluation **V** | o3 reaches 46.8% at $3.79 per query **V** | Paper CC BY 4.0 **V**; data **U** |
| **FinanceBench** ([2311.11944](https://arxiv.org/abs/2311.11944)) | Financial document QA **V** | 10,231 questions (150-case sample evaluated) **V** | No | No | — | Manual review (n = 2,400) **V** | GPT-4-Turbo with retrieval answered wrongly or refused on 81% **V** | Paper CC BY-NC-ND 4.0; repository has **no licence file** **V** |
| **FinQA** ([2109.00122](https://arxiv.org/abs/2109.00122)) | Numerical reasoning over financial reports **V** | 8,281 expert-annotated QA pairs **V** | No | No | — | Execution accuracy (65.05% for the proposed model) **V** | — | Repository **MIT** **V** |
| **ConvFinQA** ([2210.03849](https://arxiv.org/abs/2210.03849)) | Conversational numerical reasoning, built from FinQA **V** | 3,892 conversations, 14,115 questions **V** | No | No | — | U | — | Repository **MIT** **V** |
| **API-Bank** ([2304.08244](https://arxiv.org/abs/2304.08244)) | General tool use (not banking) (I) | 73 APIs; 314 dialogues; 753 calls **V** | Simulated APIs (I) | No (I) | — | U | — | Paper: arXiv non-exclusive **V** |

## 2. Matrix B: operational-reliability features
| Benchmark | Transaction integrity | Duplicate side effects | Ambiguous outcome or lost response | Stale data or transient faults injected | Recovery evaluated | Multi-agent |
|---|---|---|---|---|---|---|
| Bank-World | Postings checked (graders validated against "omitted, duplicated, and one-cent-error postings") **V** | Only in **grader validation**; duplicates *observed* as errors ("charging the fees twice") **V**. Not a controlled factor | **V-absent** (retry, idempotent, timeout, fault, inject, tool error: 0 matches) | **None injected (V-absent).** Stale *documents* and an unavailable simulated user appear as task content **V** | Not as fault recovery (I) | No (I) |
| BizAgentBench | Protection requirements (for example, reserve breach) **V** | Evaluator validated so that "an idempotent retry permitted by the tool contract should produce no additional business effect" **V**. Not injected | **Removed by design:** the harness tracks "infrastructure and simulator protocol failures separately from business outcomes, and resume[s] transient transport interruptions from the verified prior state" **V** | Outdated or conflicting *guidance* **V**; 'stale' **V-absent** | Not as fault recovery (I) | No (I) |
| Northbridge | Conduct violations (I) | **V-absent** | **V-absent** | **V-absent** | No (I) | Harness variants, including a reviewer (I) |
| TELLER-Bench | No (released evaluator) **V** | No **V** | U (paper) | U (paper) | U | U |
| τ-bench | Goal database state **V** | Not stated | **V-absent** | **V-absent** | No | No |
| **Limbo**, in "Where Does Exactly-Once Live?" ([2609.29095](https://arxiv.org/abs/2609.29095), Sep 2026; general operations, **not banking**) | **"every episode is graded against a ledger of committed effects"** **V** | **Central outcome** **V** | **Central:** "timeout_post: executed, response lost", matched with "timeout_pre: not executed", plus late commit, redelivery and partial batches (12 fault modes) **V** | Eventually consistent read paths with documented lag **V** | Yes: 15 recovery conditions, idempotency-key contracts **V** | No |
| **"Did It Happen?"**, Sun ([Research Square rs-10730245/v1](https://www.researchsquare.com/article/rs-10730245/v1), 2026-08-19; software engineering) | State success **V** | Central **V** | **Central:** "pairs these two hidden commit states behind an identical agent-visible timeout"; 81 templates **V** | — | Yes: prompt advice 0.4979, status reconciliation 0.8066, stable idempotency key 1.0000 **V** | No |
| **IdempotencyBench** ([GitHub](https://github.com/gssanjana4/idempotencybench), MIT, 2026) | U | "Benchmark measuring duplicate side effects … under retries. 320 tasks" (repository description) **V** | U | U | U | U |
| **ReliabilityBench** ([2601.06112](https://arxiv.org/abs/2601.06112)) | State-based verification oracles **V** | U | — | Timeouts, rate limits, partial responses, schema drift. Domains: scheduling, travel, customer support, e-commerce **V** | Yes (I) | No |
| **ToolMaze** ([2606.05806](https://arxiv.org/abs/2606.05806)) | — | — | — | DAG complexity × {explicit, implicit} × {transient, permanent} perturbations **V** | Yes **V** | No |
| **AgentCheck** ([2607.11098](https://arxiv.org/abs/2607.11098)) | — | — | — | 12 fault types; "stale-data faults remain near 3-4 of 10 regardless of the mitigation" **V** | Yes **V** | No |
| **Loud Failures, Quiet Failures** ([2610.10062](https://arxiv.org/abs/2610.10062)) | End-state agreement **V** | — | — | 4 typed faults injected into multi-turn BFCL; 1,920 trials. Fault-free runs agree on the end state only 63.3% of the time **V** | Yes **V** | No |

Also retrieved in full text, but checked only for the single facts below:
- **ToolMisuseBench** ([2604.01508](https://arxiv.org/abs/2604.01508)): faults include "authorization related failures" **V**.
- **ToolBench-X** ([2606.25819](https://arxiv.org/abs/2606.25819)): five hazard types **V**.

## 3. Methodology sources `[V, full text]`
### METR ([2503.14499](https://arxiv.org/abs/2503.14499), CC BY 4.0)
- **Task time:** t = the **geometric mean time of successful human baselines**.
- **Baselines:** "over 800 baselines totaling 2,529 hours, of which 558 baselines (286 successful) come from HCAST and RE-Bench, and 249 (236 successful) from" SWAA.
- **Runs:** "We perform 8 runs" per agent per task.
- **Weighting:** each task weighted by "the inverse square root of the number of tasks in the family".
- **Intervals:** 95% CI by "hierarchical bootstrap over task families, tasks, and task attempts".
- **80% horizon:** "models' 80% time horizons are 4-6x shorter" than their 50% horizons.

### Silo-Bench ([2603.01045](https://arxiv.org/abs/2603.01045); code under the Unlicense)
- **Design:** "30 algorithmic tasks across three communication complexity levels, evaluating 54 configurations over 1,620 experiments"; N ∈ {2, 5, 10, 20, 50, 100}.
- **Cost measure:** "Relative Coordination Cost (RCC) = 1−SR(N=k)/SR(N=1)".
- **Finding:** a "Communication-Reasoning Gap".

## 4. Categories
| Category | Members |
|---|---|
| Financial document QA | FinanceBench |
| Financial numerical reasoning | FinQA, ConvFinQA |
| Financial tool use (read-only) | FinToolBench, Finance Agent Benchmark, FinGAIA (partly `U`) |
| General tool use | API-Bank |
| **Stateful banking and payments workflows** | **Bank-World, BizAgentBench, Northbridge**; TELLER-Bench (sequence-graded) |
| **Operational reliability: faults, ambiguity, idempotency** | **Limbo, "Did It Happen?", IdempotencyBench**, ReliabilityBench, ToolMaze, AgentCheck, Loud/Quiet, ToolMisuseBench, ToolBench-X |
| Repeated-trial consistency | τ-bench (pass^k), Bank-World, BizAgentBench, ReliabilityBench |
| Long-horizon methodology | METR; BankerToolBench (human-timed deliverables) |
| Multi-agent distributed coordination | Silo-Bench (algorithmic tasks) |

## 5. Licence and reuse (a paper licence is **not** a data licence)
| Source | Code / data licence `V` | Reuse rule for BankOps |
|---|---|---|
| BizAgentBench | MIT (repository and Hugging Face) | Usable through an attributed external adapter; never copied into private splits |
| τ-bench, FinQA, ConvFinQA, IdempotencyBench, "Did It Happen?" artifact, AgentCheck | MIT | Methods and code patterns may be adapted with attribution |
| BankerToolBench, Gandalf grader | Apache-2.0 | Same, keeping the NOTICE |
| Silo-Bench | Unlicense | Free reuse |
| TELLER-Bench | **Conflict: CC0 vs CC BY 4.0** | Treat as **CC BY 4.0** (the stricter) until clarified |
| FinToolBench, FinGAIA, FinanceBench, ToolMaze | **No licence file** | **No reuse** of code or data |
| Northbridge code | NOASSERTION | **No reuse** until the licence is read |
| Bank-World | Environments private | Methodology citation only |

## 6. Limitations of this review
- **TELLER-Bench:** the paper body was not accessible.
- **Absence claims** are keyword-based (see the labels above).
- **Not re-verified:** τ-bench task counts and the per-category task counts of Bank-World.
- **Coverage:** the search covered arXiv, OpenReview, Research Square and GitHub. Industry reports and paywalled venues were not searched.
- **Changing field:** this area moves monthly (most overlap papers are from Jul–Nov 2026). Re-run the search before any publication.
