# BankOps public API and external submissions

**Revised 2026-11-17 (Day 2).**
- **Status:** design.
- **Week-1 implementation:** read-only routes on the existing **loopback** service only.
- **Run creation:** stays CLI-only in week 1.

**Labels:**
- Every endpoint, schema and pipeline step below is a **DECISION**: a proposed design, not built.
- The loopback-only, unauthenticated live service and its hardened request guards (PR #14) are **VERIFIED** existing behaviour.
- Any claim that model identity is verified **without** gateway proxying would be **UNKNOWN**, and is never asserted.

## 1. Credential references (consistent with the readiness document §8)
| Context | Reference form | Resolved where |
|---|---|---|
| Local (week 1 onward) | `env:NAME` | In the backend process, when a request is sent |
| Hosted (future) | `vault:<org>/<name>` | In the job runner, from a secret store. The secret value never appears in API responses, logs, manifests or results |

The Day 1 version used `org-secret:` in one example and `env:` elsewhere. This table replaces both.

## 2. API v1 (target, not built)
| Method | Path | Scope | Purpose |
|---|---|---|---|
| POST | `/v1/evaluations` | `submit` | Create a job from a configuration (product spec §3). Returns `config_sha` and the compatibility verdict |
| GET | `/v1/evaluations/{id}` | `read:own` | Status (queued, running, completed, failed, rejected) and progress |
| GET | `/v1/evaluations/{id}/results` | `read:own` / `read:public` | Aggregates with intervals, by equivalence class |
| GET | `/v1/evaluations/{id}/results/detail` | `read:own` | Per-episode verdicts, sanitized. No held-out task text |
| GET | `/v1/evaluations/{id}/provenance` | `read:own` | Manifest summary and hashes |
| GET | `/v1/evaluations/{id}/manifest` | `read:own` | The self-hashed manifest |
| GET | `/v1/leaderboard?suite=&env=&toolset=&world=` | public | Admitted aggregates for one equivalence class |

- **Incompatible configurations:** `422 {"error":"incompatible_config","missing_tools":{task:[tools]}}`. Never auto-fixed.

## 3. Authentication and limits (future hosted service)
- **Keys:** organisation API keys, stored hashed, with scopes.
- **Limits:**
  - per-key rate limits and concurrency;
  - per-job step, token and cost caps;
  - request size limits, reusing the hardened request handling from PR #14.

## 4. External submission pipeline: safety requirements
1. **Adapter contract:** the agent is an HTTP endpoint or container implementing `POST /act {observation, tool_schemas, budget} -> {tool_call | finish}`. The environment stays on our side.
2. **Sandbox:** an isolated container or VM. Network egress only to our gateway (and to a declared model provider, if allowed). CPU, memory and wall-clock limits. No access to graders, fixtures or other jobs. **Never executed inside the trusted backend process.**
3. **Tool mediation:** every call passes through the registry dispatcher (permission checks, audit events).
4. **Identity:** the declared model id and scaffold version are recorded. They are marked **unverified** unless model calls are proxied through our gateway.
5. **Integrity:** only our grader produces results; signed manifests.
6. **Provenance:** a submission record holds the adapter digest, configuration hash, submitter and timestamps.
7. **Duplicates:** keyed by `(adapter digest, model id, config_sha)`. Re-runs count as replicates.
8. **Abuse:** quotas; held-out text is never echoed; responses are scanned for canary strings.
9. **Public/private separation:** private by default; publishing to the leaderboard is an explicit, reviewed action.

## 5. Week-1 slice
- **Routes:** `GET /api/bankops/runs/{run_id}` and `GET /api/bankops/results?class=…`.
- **Data:** sanitized aggregates, verdicts and a manifest summary.
- **Server:** loopback only, using the existing request guards.
