# Live runs dashboard (local)

Status: working vertical slice, local and private only. Added 2026-11-16.
Not deployed. Not part of the published static site's data.

## What it is

```
runner.run_experiment(event_sink=...)        raw rows + trajectories (JSONL, unchanged)
        │  hooks: experiment_started / trial_started / trial_finished / experiment_finished|failed
        ▼
live.recorder.LiveRecorder ── live.sanitize ──►  SANITIZATION BOUNDARY (allowlist)
        ▼
live.store.RunStore (SQLite, WAL)            sanitized run metadata + events only
        ▼
live.server (http.server, loopback)          JSON API + Server-Sent Events
        ▼
frontend /live, /live/runs/:id               React pages (fetch + EventSource)
```

Standard library only on the Python side; no new frontend dependency.

## Run it

```powershell
# 1. API (default database: %USERPROFILE%\.arl\live\runs.sqlite, outside the repo)
$env:PYTHONPATH = "src"
python -m minilab.live.server --enable-synthetic      # --enable-synthetic is for development only

# 2. Frontend (Vite proxies /api to 127.0.0.1:8765)
cd frontend; npm run dev        # open http://localhost:5173/live

# 3. Record a real run into the same database (adds no scoring change)
python -m minilab.runner --model scripted --live-db $env:USERPROFILE\.arl\live\runs.sqlite
```

Single process alternative: `npm run build`, then
`python -m minilab.live.server --static frontend/dist` and open `http://127.0.0.1:8765/live`.

## API (version 1)

| Method and path | Returns |
|---|---|
| `GET /api/health` | status, API version, code revision, last event id, auth statement |
| `GET /api/experiments` | registry: status, progress, current task, elapsed, staleness |
| `GET /api/experiments/{id}` | experiment, sanitized trials, analytics |
| `GET /api/experiments/{id}/events?after=N` | events of one run (polling fallback) |
| `GET /api/events?after=N` | all events after a cursor |
| `GET /api/stream?after=N` | SSE; honours `Last-Event-ID`; keepalive comments |
| `GET /api/compare?a=&b=` | descriptive difference of two runs' pass rates |
| `POST /api/dev/synthetic-run` | only with `--enable-synthetic` and header `X-ARL-Local: 1` |

Run status: `queued`, `running`, `completed`, `failed`. A running run with no update for
120 s is flagged `stale` (it may be in a long model call); it is never marked failed automatically.

## Data layers and what is never served

1. Raw execution evidence (trajectories, result rows): stays in JSONL. Not stored in the live
   database, not served.
2. Sanitized run metadata (`live/sanitize.py`, allowlist): ids, task id, status, verdict, failure
   category, termination reason, evaluator reason *categories*, counts, tool *names* with outcome,
   provenance counts and statuses.
3. Aggregated analytics (`live/analytics.py`).
4. Public material: not decided here (owner decision U9).

Never crosses the boundary: prompts, model messages, tool arguments, observations, final answers,
raw provider errors, evaluator reason strings, file paths, exception messages. `assert_safe`
re-checks every write to the store. The live server refuses `*.traj.json` even when a built
frontend containing the legacy exports is served through `--static`.

## Security

- No authentication. The server binds to loopback only and refuses any other address.
- `Host` must be a loopback name (DNS rebinding); no CORS headers are sent; the one POST needs a
  custom header.
- **Do not deploy or expose this service until authentication and an access policy exist.**
  The published site (Vercel) has no backend; there `/live` reports "backend not reachable".

## Recording integrity (added in the hardening pass)

A sink failure never changes a run (`runner._SinkGuard`, also under `-W error`). Every failed
write is counted and, when the store can still be written, noted durably. Each run carries
`recording`: `complete`, `incomplete`, `unavailable` or `in_progress`.

- `incomplete`: a write failed or the stored records do not reconcile. The UI shows a red notice.
- `unavailable`: completeness cannot be established (record never closed, or a pre-existing row).
- This is about the dashboard record only. It is never an agent failure, and the JSONL files stay
  authoritative. If the store cannot be written at all, nothing is recorded and the API has no such run.

## Run manifest

Built once at run start (`run_manifest.py`): commit, clean/dirty working tree (scope: `src`,
`data/bank.json`, `pyproject.toml`), source/fixture/task/config hashes, Python, platform, adapter,
and a protocol revision only if passed with `--protocol-ref`. A dirty tree is cited as
`minilab-src@WT#<sha8>`, never as the commit. `--manifest-out <new file>` writes it beside results.

If `--manifest-out` cannot be written (existing file, missing folder, bad path) the run still
proceeds; the failure is a warning, a line on stderr (`MANIFEST NOT WRITTEN`), a field in
`run_info` and `manifest_persistence: failed` in the live record. A manifest that could not be
BUILT (`manifest.status: unavailable`) is a different condition from one that could not be SAVED.
If `--live-db` cannot be opened the run proceeds without recording and prints `LIVE RECORDING DISABLED`.

## Fault origin

Result rows of new runs carry `fault_origin`, `fault_attribution` (`established` /
`unattributed`), `fault_basis`, `fault_boundary` and `fault_exception_type` (non-scoring).
An SDK exception class is not treated as proof of provider fault: only an HTTP 429/5xx status read
from the exception object is `model_service_error`; 4xx, transport failures, SDK-side errors and
local exceptions are reported as unattributed. Trial status and the agent failure taxonomy are unchanged.

## Analytics rules

- Pass rates use valid trials only; excluded trials are counted separately.
- No valid trial: no rate. Fewer than 5 valid trials: no interval ("insufficient sample");
  5 is a display convention of this dashboard, not a statistical rule.
- Intervals are Wilson and are shown only with at most one valid trial per task; with repeated
  trials of a task the interval is suppressed.
- Comparisons are descriptive. A Newcombe-Wilson interval appears only when the two runs share
  no task and neither repeats a task; runs sharing tasks are paired, so no interval is shown.
  Nothing is called significant.
- Depth and family are shown only when declared. Legacy tasks have no declared depth; the two
  unreviewed candidate depths appear in a tooltip and are not used for grouping.

## Relation to the existing static dashboard (migration)

- Unchanged: `/`, `/dashboard`, `/experiments/...`, `frontend/public/data/*`, `scripts/export-data.mjs`
  (not run), all existing tests.
- Added: `/live`, `/live/runs/:id`, a "Live runs" navigation link, a dev-only Vite proxy.
- The live pages never request `/data/*`; the legacy pages never call `/api/*`.
- Possible later steps (not done): import historical result rows into the live store through the
  same sanitizer; then retire the raw trajectory exports after the U9 review.

## Synthetic runs

`POST /api/dev/synthetic-run` replays the built-in offline `scripted` or `stub` model through the
real runner into a temporary directory. No provider is called, nothing is written to `results/`,
and the run is stored and displayed as `synthetic`. Its numbers test the pipeline only.

## Tests

- `tests/test_live_backend.py`: sink contract, sanitization, store, API, SSE, reconnection, failed
  and stale runs, raw-evidence scans of responses and of the database file.
- `frontend/src/live/__tests__/live.test.tsx`: pages against fetch/EventSource doubles.
- `frontend/e2e/live-e2e.mjs`: real browser, real service, synthetic runs (needs Playwright; not
  part of `npm test`).

## Known limits

- Progress granularity is one event per trial start/finish; there are no per-step events.
- The store has no retention policy and no migration tooling (schema version 1).
- Staleness is a time heuristic; there is no process heartbeat.
- Provider/model names are passed through a label filter, not a registry.
