# BankOps commercial validation plan

**Revised 2026-11-17 (Day 2).** Every commercial statement here is a **HYPOTHESIS**. No market size, revenue, pricing, funding or demand figure is claimed.

## 1. Competitive facts (VERIFIED, from `docs/research/BANKING_BENCHMARK_LANDSCAPE.md`)
- **Open, permissively licensed alternatives exist:**
  - BizAgentBench (MIT; payments workflows);
  - τ-bench (MIT);
  - IdempotencyBench (MIT; duplicate side effects under retries);
  - the "Did It Happen?" artifact (MIT).
- **Bank-World keeps its environments, graders and trajectories private.** Its commercial intent is **UNKNOWN**; do not infer one.
- **Limbo and "Did It Happen?" are research results, not products.** Their findings (idempotency contracts dominate; agents misreport duplicates) are public knowledge that buyers can act on without us.

**Implication (INFERRED):** a buyer will not pay for "a banking benchmark" as such. Paid value, if any, must come from:
1. private, contamination-resistant suites;
2. **their** workflows, controls and policies;
3. evidence that model-risk or audit stakeholders accept;
4. repeatable regression evaluation of their own agent releases.

## 2. Hypotheses
| ID | Hypothesis | Validating evidence | Invalidating evidence |
|---|---|---|---|
| H1 | Teams deploying agents in banking operations lack credible, workflow-specific reliability evidence | At least 8 of 15 interviews describe an unresolved evaluation gap in their own words | Most have adequate internal evaluations |
| H2 | They value fault and control testing (duplicates, approvals, uncertain outcomes) over generic scores | Interviewees rank these among their top 3 risks **and** report that existing open benchmarks do not cover their controls | Ranked low, or open tools suffice |
| H3 | Independent third-party evaluation matters to risk or compliance | Risk or compliance roles ask for reports; procurement requires independent validation | Internal validation is sufficient |
| H4 | Custom private suites are a paid need | At least 2 design partners agree to scope a paid pilot | Interest only in free public results |
| H5 | Model providers want placement on a banking leaderboard | Inbound requests, or agreement to submit | No engagement |

## 3. Target roles
- **Banks:** model-risk management, operations transformation, payments operations, internal audit.
- **Fintech and banking-software vendors:** heads of AI, AI product leads.
- **Enterprise AI platform teams.**
- **Model providers:** evaluation and partnerships teams.

## 4. Interview guide (30 minutes; no pitch for the first 20)
1. Which agent workflows touch money movement or customer records, in production or pilot?
2. How do you decide such an agent is reliable enough? What evidence goes to risk or compliance?
3. Describe the last consequential agent failure. What did detection and remediation cost (time, money, effort)?
4. How do you test retries after timeouts, duplicate payments, and actions taken while an approval is pending?
5. What do you use today (internal tests, vendor claims, open benchmarks such as τ-bench or BizAgentBench, consultants)? What is missing?
6. Who would own buying an external evaluation, and what would it need to show?
7. Could you share anonymised workflows or controls to build a private suite? What would block that (legal, data, security)?

## 5. Plan and decision gate
- **Weeks 1–4:** 15 interviews (at least 3 per role group), coded against H1–H5.
- **Continue the commercial track** only if H1 **and** (H2 or H4) are validated.
- **No pricing or projections** until at least 2 design-partner conversations reach scoping.
