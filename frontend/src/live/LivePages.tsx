/**
 * Live Runs: pages backed by the local live API (never by the static exports
 * under /data). Everything shown comes from /api/*; nothing here is a
 * placeholder number. When the backend is not reachable the pages say so.
 */
import * as React from "react";
import { Link, useParams } from "react-router-dom";
import { LabShell } from "../layouts/LabShell";
import { Pill, Tip } from "../components/anim/primitives";
import { Meta, SectionTitle } from "../components/display";
import { api, useLive, useLiveFeed } from "./api";
import type { Connection } from "./api";
import type {
  Comparison, LiveAnalytics, LiveEvent, LiveExperiment, LiveTrial, RateBlock, Unavailable,
} from "./types";

const pct = (x: number | null) => (x === null ? "n/a" : `${(x * 100).toFixed(1)}%`);
const clock = (ts: number | null) => (ts ? new Date(ts * 1000).toLocaleString() : "n/a");
const short = (id: string) => (id.length > 14 ? `${id.slice(0, 14)}…` : id);

export function duration(s: number | null): string {
  if (s === null || !Number.isFinite(s)) return "n/a";
  if (s < 60) return `${s.toFixed(1)} s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m} min ${Math.round(s % 60)} s` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

const CONNECTION: Record<Connection, { tone: "pass" | "warn" | "fail" | "neutral"; text: string }> = {
  connecting: { tone: "neutral", text: "connecting" },
  live: { tone: "pass", text: "live" },
  reconnecting: { tone: "warn", text: "reconnecting" },
  offline: { tone: "fail", text: "offline" },
};

export function ConnectionBadge({ connection }: { connection: Connection }) {
  const c = CONNECTION[connection];
  return (
    <span role="status" aria-live="polite" data-testid="connection" data-connection={connection}
      className="inline-flex items-center gap-2 font-mono text-xs">
      event stream <Pill tone={c.tone}>{c.text}</Pill>
    </span>
  );
}

function StatusPill({ exp }: { exp: LiveExperiment }) {
  const tone = exp.status === "completed" ? "pass" : exp.status === "failed" ? "fail"
    : exp.stale ? "warn" : "neutral";
  return <Pill tone={tone}>{exp.stale ? `${exp.status} · no recent update` : exp.status}</Pill>;
}

const RECORDING_TONE = { complete: "pass", incomplete: "fail", unavailable: "warn", in_progress: "neutral" } as const;

export function RecordingBadge({ exp }: { exp: LiveExperiment }) {
  const r = exp.recording;
  return (
    <span data-testid="recording-badge" data-recording={r.status}>
      <Pill tone={RECORDING_TONE[r.status]}>recording {r.status.replace("_", " ")}</Pill>
    </span>
  );
}

function RecordingNotice({ exp }: { exp: LiveExperiment }) {
  const r = exp.recording;
  if (r.status === "complete" || r.status === "in_progress") return null;
  return (
    <div role="alert" data-testid="recording-notice"
      className="card-flat border-2 border-[var(--color-brick)] p-3 text-sm">
      <p className="font-bold">
        {r.status === "incomplete"
          ? "This dashboard record is incomplete."
          : "The completeness of this dashboard record could not be established."}
      </p>
      <p className="mt-1 text-[var(--color-ink-soft)]">
        Some events or trial records may be missing here. This is a recording problem, not an agent
        failure, and it does not change any result. The authoritative evidence is the{" "}
        {r.authoritative_evidence}.
      </p>
      <ul className="mt-1 list-disc pl-5 font-mono text-xs">
        {r.reasons.map((x) => <li key={x}>{x}</li>)}
        {Object.entries(r.failures_by_hook).map(([hook, n]) => (
          <li key={hook}>{hook.replace(/_/g, " ")}: {n} failed write(s)</li>
        ))}
      </ul>
    </div>
  );
}

function KindBadge({ exp }: { exp: LiveExperiment }) {
  return exp.kind === "synthetic" ? <Pill tone="warn">synthetic</Pill> : null;
}

function ProgressBar({ exp }: { exp: LiveExperiment }) {
  const { finished, total, fraction } = exp.progress;
  const width = fraction === null ? 0 : Math.round(fraction * 100);
  return (
    <div>
      <div role="progressbar" aria-label="Trials finished" aria-valuemin={0}
        aria-valuemax={total ?? undefined} aria-valuenow={finished}
        aria-valuetext={total === null ? `${finished} trials finished` : `${finished} of ${total} trials finished`}
        className="h-3 w-full overflow-hidden rounded-full border border-[var(--color-line)] bg-[var(--color-paper-deep)]">
        <div className="h-full bg-[var(--color-signal)]" style={{ width: `${width}%` }} />
      </div>
      <p className="tnum mt-1 font-mono text-xs text-[var(--color-ink-soft)]" data-testid="progress-text">
        {total === null ? `${finished} trials finished (total unknown)` : `${finished} / ${total} trials finished`}
        {exp.progress.remaining !== null && exp.status !== "completed" && ` · ${exp.progress.remaining} remaining`}
      </p>
    </div>
  );
}

function Offline({ what }: { what: string }) {
  return (
    <div className="card-flat p-6" role="alert" data-testid="backend-offline">
      <p className="font-display text-lg font-bold">Live backend not reachable</p>
      <p className="mt-2 text-sm text-[var(--color-ink-soft)]">
        {what} comes from a local service that is not answering. Nothing is shown in its place.
        Start it on this machine with <code className="font-mono">python -m minilab.live.server</code>.
        The published site has no live backend by design.
      </p>
      <p className="mt-3 text-sm">
        Looking for the earlier experiments? They are static exports:{" "}
        <Link className="underline" to="/experiments">legacy exported experiments</Link>.
      </p>
    </div>
  );
}

function RateLine({ block, label, caveat }: { block: RateBlock; label: string; caveat?: string | null }) {
  return (
    <div data-testid={`rate-${label}`}>
      <p className="font-mono text-[11px] uppercase tracking-wider text-[var(--color-ink-faint)]">{label}</p>
      <p className="tnum font-display text-3xl font-bold">
        {block.rate === null ? "no valid trials" : pct(block.rate)}
      </p>
      <p className="tnum font-mono text-xs text-[var(--color-ink-soft)]">
        {block.passed} passed of {block.valid} valid
        {block.interval && ` · ${Math.round(block.interval.confidence * 100)}% Wilson interval ${pct(block.interval.lower)} to ${pct(block.interval.upper)}`}
      </p>
      {block.insufficient_sample && block.valid > 0 && (
        <p className="mt-1"><Pill tone="warn">insufficient sample</Pill></p>
      )}
      {caveat && (
        <p className="mt-1" data-testid="rate-recording-caveat"><Pill tone="warn">recording not complete</Pill></p>
      )}
      {block.notes.map((n) => (
        <p key={n} className="mt-1 text-xs text-[var(--color-ink-soft)]">{n}</p>
      ))}
    </div>
  );
}

function Bars({ data, empty }: { data: Record<string, number>; empty: string }) {
  const entries = Object.entries(data);
  const max = Math.max(1, ...entries.map(([, n]) => n));
  if (entries.length === 0) return <p className="font-mono text-sm text-[var(--color-ink-faint)]">{empty}</p>;
  return (
    <ul className="space-y-2">
      {entries.map(([k, n]) => (
        <li key={k} className="grid grid-cols-[minmax(0,12rem)_1fr_3rem] items-center gap-3 font-mono text-xs">
          <span className="truncate">{k.replace(/_/g, " ")}</span>
          <span className="h-2 rounded bg-[var(--color-paper-deep)]">
            <span className="block h-2 rounded bg-[var(--color-ink)]" style={{ width: `${(n / max) * 100}%` }} />
          </span>
          <span className="tnum text-right">{n}</span>
        </li>
      ))}
    </ul>
  );
}

function NotAvailable({ what, info }: { what: string; info: Unavailable }) {
  return (
    <p className="text-sm text-[var(--color-ink-soft)]" data-testid={`unavailable-${what}`}>
      <span className="font-mono text-xs uppercase tracking-wider">{what}: not available.</span> {info.reason}
    </p>
  );
}

function EventList({ events }: { events: LiveEvent[] }) {
  if (events.length === 0) {
    return <p className="font-mono text-sm text-[var(--color-ink-faint)]">No events received in this session yet.</p>;
  }
  return (
    <ol className="max-h-72 space-y-1 overflow-auto font-mono text-xs" data-testid="event-list" aria-label="Latest events">
      {events.slice(0, 50).map((e) => (
        <li key={e.id} className="grid grid-cols-[4rem_10rem_1fr] gap-2">
          <span className="tnum text-[var(--color-ink-faint)]">#{e.id}</span>
          <span>{e.type.replace(/_/g, " ")}</span>
          <span className="truncate text-[var(--color-ink-soft)]">
            {typeof e.payload.task_id === "string" ? e.payload.task_id : short(e.experiment_id)}
          </span>
        </li>
      ))}
    </ol>
  );
}

function RunRow({ exp }: { exp: LiveExperiment }) {
  return (
    <li className="card-flat p-4" data-testid="run-row" data-status={exp.status}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Link to={`/live/runs/${exp.experiment_id}`} className="font-display text-lg font-bold underline-offset-4 hover:underline">
          {exp.config.label ?? short(exp.experiment_id)}
        </Link>
        <span className="flex flex-wrap items-center gap-2"><KindBadge exp={exp} /><RecordingBadge exp={exp} /><StatusPill exp={exp} /></span>
      </div>
      <p className="mt-1 font-mono text-xs text-[var(--color-ink-soft)]">
        {exp.config.model ?? "model n/a"} · {exp.config.exec_mode} · {exp.config.task_ids.length} tasks ·
        updated {clock(exp.updated_at)}
      </p>
      <div className="mt-3"><ProgressBar exp={exp} /></div>
      {exp.status === "running" && exp.current_task && (
        <p className="mt-1 font-mono text-xs">current task: {exp.current_task} · elapsed {duration(exp.elapsed_s)}</p>
      )}
      {exp.stale && <p className="mt-1 text-xs text-[var(--color-amber-deep)]">{exp.stale_reason}</p>}
      {exp.error && <p className="mt-1 font-mono text-xs text-[var(--color-brick)]">run error: {exp.error.error_type}</p>}
    </li>
  );
}

export function LiveOverviewPage() {
  const feed = useLiveFeed();
  const list = useLive(api.experiments, feed.tick);
  const health = useLive(api.health, feed.tick);
  const [starting, setStarting] = React.useState<string | null>(null);
  const [startError, setStartError] = React.useState<string | null>(null);
  const runs = list.data?.experiments ?? [];
  const active = runs.filter((r) => r.status === "queued" || r.status === "running");
  const history = runs.filter((r) => r.status === "completed" || r.status === "failed");
  const offline = list.error !== null && list.data === null;

  const start = (model: string) => {
    setStarting(model);
    setStartError(null);
    api.startSynthetic({ model, delay_s: 0.4 })
      .catch((e: unknown) => setStartError(String(e)))
      .finally(() => setStarting(null));
  };

  return (
    <LabShell>
      <div className="space-y-10">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <p className="font-mono text-xs uppercase tracking-[0.25em] text-[var(--color-signal)]">
              live backend · local only
            </p>
            <h2 className="font-display text-4xl font-bold tracking-tight sm:text-5xl">Live runs</h2>
            <p className="mt-2 max-w-2xl text-sm text-[var(--color-ink-soft)]">
              Runs recorded by the local live service as they execute. This page does not read the
              static exports; those are under <Link className="underline" to="/experiments">legacy exported experiments</Link>.
            </p>
          </div>
          <ConnectionBadge connection={offline ? "offline" : feed.connection} />
        </div>

        {list.loading && list.data === null && <p className="font-mono text-sm" role="status">Loading runs…</p>}
        {offline && <Offline what="The run list" />}
        {list.error !== null && list.data !== null && (
          <p role="alert" className="card-flat p-3 font-mono text-xs text-[var(--color-amber-deep)]">
            The backend stopped answering; showing the last data received.
          </p>
        )}

        {!offline && list.data !== null && (
          <>
            <section aria-label="Active runs">
              <SectionTitle index="01" title="Active runs" hint={`${active.length} queued or running`} />
              {active.length === 0 ? (
                <p className="card-flat p-6 font-mono text-sm text-[var(--color-ink-faint)]" data-testid="idle">
                  Idle: no run is queued or running.
                </p>
              ) : (
                <ul className="space-y-3">{active.map((r) => <RunRow key={r.experiment_id} exp={r} />)}</ul>
              )}
            </section>

            <section aria-label="Run history">
              <SectionTitle index="02" title="Run history" hint={`${history.length} finished`} />
              {history.length === 0 ? (
                <p className="card-flat p-6 font-mono text-sm text-[var(--color-ink-faint)]" data-testid="no-history">
                  No finished runs recorded yet. Record one with{" "}
                  <code>python -m minilab.runner --live-db &lt;file&gt;</code>.
                </p>
              ) : (
                <ul className="space-y-3">{history.map((r) => <RunRow key={r.experiment_id} exp={r} />)}</ul>
              )}
            </section>

            <section aria-label="Latest events">
              <SectionTitle index="03" title="Event stream" hint="this browser session" />
              <div className="card p-5"><EventList events={feed.events} /></div>
            </section>

            <section aria-label="System health">
              <SectionTitle index="04" title="System health" />
              {health.data ? (
                <dl className="card grid grid-cols-2 gap-4 p-5 sm:grid-cols-4">
                  <Meta label="api" value={`v${health.data.api_version} · ${health.data.status}`} />
                  <Meta label="code revision" value={health.data.code_revision ? health.data.code_revision.slice(0, 10) : "n/a"} />
                  <Meta label="authentication" value={health.data.authentication}
                    tip="No authentication exists. The service must stay on this machine." />
                  <Meta label="uptime" value={duration(health.data.uptime_s)} />
                  <Meta label="events recorded" value={String(health.data.last_event_id)} />
                  <Meta label="data served" value={health.data.data_layers_served.join(", ")}
                    tip="Prompts, tool arguments, observations and answers are never served." />
                </dl>
              ) : <p className="font-mono text-sm text-[var(--color-ink-faint)]">Health not available.</p>}
            </section>

            {health.data?.synthetic_runs_enabled && (
              <section aria-label="Development tools">
                <SectionTitle index="05" title="Development" hint="synthetic plumbing runs only" />
                <div className="card-flat space-y-3 p-5">
                  <p className="text-sm text-[var(--color-ink-soft)]">
                    A synthetic run replays a built-in offline model through the real runner. It calls no
                    provider, is not an experiment, and its pass rate says nothing about any agent.
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {["scripted", "stub"].map((m) => (
                      <button key={m} type="button" disabled={starting !== null} onClick={() => start(m)}
                        className="rounded-md border-2 border-[var(--color-line)] px-3 py-1.5 font-mono text-xs uppercase tracking-wider disabled:opacity-50">
                        {starting === m ? "starting…" : `start synthetic run (${m})`}
                      </button>
                    ))}
                  </div>
                  {startError && <p role="alert" className="font-mono text-xs text-[var(--color-brick)]">Could not start: {startError}</p>}
                </div>
              </section>
            )}
          </>
        )}
      </div>
    </LabShell>
  );
}

function TrialTable({ trials }: { trials: LiveTrial[] }) {
  if (trials.length === 0) return <p className="font-mono text-sm text-[var(--color-ink-faint)]">No trial has started.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[46rem] text-left font-mono text-xs" data-testid="trial-table">
        <caption className="sr-only">Trials of this run, in execution order</caption>
        <thead className="uppercase tracking-wider text-[var(--color-ink-faint)]">
          <tr>
            <th scope="col" className="py-2 pr-3">#</th><th scope="col" className="pr-3">task</th>
            <th scope="col" className="pr-3">state</th><th scope="col" className="pr-3">verdict</th>
            <th scope="col" className="pr-3">failure category</th><th scope="col" className="pr-3">ended by</th>
            <th scope="col" className="pr-3">calls</th><th scope="col">tool activity</th>
          </tr>
        </thead>
        <tbody>
          {trials.map((t) => (
            <tr key={t.run_id} className="border-t border-[var(--color-paper-deep)] align-top" data-testid="trial-row">
              <td className="tnum py-2 pr-3">{t.seq ?? ""}</td>
              <td className="pr-3">{t.task_id}</td>
              <td className="pr-3">
                {t.state === "running" ? <Pill tone="neutral">running</Pill>
                  : t.state === "unrecorded" ? <Pill tone="fail">finish not recorded</Pill>
                  : t.trial_status === "valid" ? "finished" : <Pill tone="warn">{(t.trial_status ?? "unknown").replace(/_/g, " ")}</Pill>}
              </td>
              <td className="pr-3">
                {t.state !== "finished" ? "" : t.passed === true ? <Pill tone="pass">pass</Pill>
                  : t.passed === false ? <Pill tone="fail">fail</Pill> : "excluded"}
              </td>
              <td className="pr-3">{t.state === "finished" && t.passed === false ? (t.failure ?? "").replace(/_/g, " ") : ""}</td>
              <td className="pr-3">
                {(t.termination_reason ?? "").replace(/_/g, " ")}
                {t.fault_origin && !["none", "not_recorded"].includes(t.fault_origin) && (
                  <span className="block text-[var(--color-ink-faint)]" data-testid="fault-origin"
                    title={`Observed via: ${(t.fault_basis ?? "none").replace(/_/g, " ")}`}>
                    fault: {t.fault_origin.replace(/_/g, " ")}
                    {t.fault_attribution === "unattributed" && " (cause not established)"}
                  </span>
                )}
              </td>
              <td className="tnum pr-3">{t.tool_calls ?? ""}</td>
              <td>
                <span className="flex flex-wrap gap-1">
                  {(t.tool_activity ?? []).map((a, i) => (
                    <span key={i} title={`iteration ${a.iteration ?? "?"}: ${a.outcome}`}
                      className={`rounded border px-1 ${a.outcome === "error" ? "border-[var(--color-brick)] text-[var(--color-brick)]" : "border-[var(--color-ink-faint)]"}`}>
                      {a.tool.replace(/^get_/, "")}
                    </span>
                  ))}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TaskTable({ analytics }: { analytics: LiveAnalytics }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[40rem] text-left font-mono text-xs" data-testid="task-table">
        <caption className="sr-only">Results per task</caption>
        <thead className="uppercase tracking-wider text-[var(--color-ink-faint)]">
          <tr>
            <th scope="col" className="py-2 pr-3">task</th><th scope="col" className="pr-3">family</th>
            <th scope="col" className="pr-3">declared depth</th><th scope="col" className="pr-3">trials</th>
            <th scope="col" className="pr-3">valid</th><th scope="col" className="pr-3">excluded</th>
            <th scope="col" className="pr-3">passed</th><th scope="col">pass rate</th>
          </tr>
        </thead>
        <tbody>
          {analytics.by_task.map((t) => (
            <tr key={t.task_id} className="border-t border-[var(--color-paper-deep)]">
              <td className="py-2 pr-3">{t.task_id}</td>
              <td className="pr-3">{t.family ?? "not defined"}</td>
              <td className="pr-3">
                {t.declared_depth ?? (
                  <Tip text={`${t.depth_status}${t.candidate_depths ? `. Unreviewed candidates: ${Object.entries(t.candidate_depths).map(([k, v]) => `${k}=${v}`).join(", ")}` : ""}`}>
                    undeclared
                  </Tip>
                )}
              </td>
              <td className="tnum pr-3">{t.trials}</td><td className="tnum pr-3">{t.valid}</td>
              <td className="tnum pr-3">{t.excluded}</td><td className="tnum pr-3">{t.passed}</td>
              <td className="tnum">{t.rate === null ? "n/a" : pct(t.rate)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function fieldText<T>(f: { status: string; value: T | null; reason?: string } | undefined,
  show: (v: T) => string): string {
  if (!f) return "not recorded";
  if (f.status !== "available" || f.value === null) return `unavailable${f.reason ? ` (${f.reason})` : ""}`;
  return show(f.value);
}

function ManifestFile({ exp }: { exp: LiveExperiment }) {
  const p = exp.config.manifest_persistence;
  if (!p || p.status === "not_recorded" || p.status === "not_requested") return null;
  return p.status === "failed" ? (
    <p role="alert" className="card-flat border-2 border-[var(--color-brick)] p-3 text-sm" data-testid="manifest-file-failed">
      <strong>The manifest file was not written</strong> ({p.error_type ?? "unknown error"}). The run was not
      affected, but no manifest file exists for it; only the record shown here does.
    </p>
  ) : (
    <p className="font-mono text-xs text-[var(--color-ink-soft)]" data-testid="manifest-file-written">manifest file: written</p>
  );
}

export function SourceProvenance({ exp }: { exp: LiveExperiment }) {
  const m = exp.config.manifest;
  if (!m || m.status !== "available") {
    return (
      <div className="space-y-3">
        <p className="card-flat p-3 text-sm" data-testid="manifest-unavailable">
          <strong>Source provenance unavailable.</strong>{" "}
          {m?.reason ?? "No run manifest was recorded for this run."} The commit shown, if any, does not
          establish which source actually ran.
        </p>
        <ManifestFile exp={exp} />
      </div>
    );
  }
  const tree = m.worktree?.status === "available" ? m.worktree.value : null;
  const state = tree?.state ?? "unknown";
  return (
    <div className="space-y-3" data-testid="manifest" data-worktree={state}>
      {state === "dirty" && (
        <p role="alert" className="card-flat border-2 border-[var(--color-amber-deep)] p-3 text-sm" data-testid="dirty-tree">
          <strong>Working tree was not clean.</strong> This run used source that differs from the commit
          ({tree?.changed_tracked_files ?? "?"} changed tracked file(s), {tree?.untracked_files ?? "?"} untracked,
          within {tree?.scope.join(", ")}). It is <strong>not</strong> a reproduction of that commit; the
          source hash below identifies what ran.
        </p>
      )}
      <ManifestFile exp={exp} />
      {state === "unknown" && (
        <p role="alert" className="card-flat p-3 text-sm" data-testid="unknown-tree">
          <strong>Clean or dirty: not established.</strong> {fieldText(m.worktree, () => "")}
        </p>
      )}
      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <Meta label="source" value={fieldText(m.source_ref, (v) => v)}
          tip="name@commit#hash for a clean tree; name@WT#hash when the working tree differs from the commit." />
        <Meta label="working tree" value={state} />
        <Meta label="commit" value={fieldText(m.git_commit, (v) => v.slice(0, 10))} />
        <Meta label="tasks" value={fieldText(m.tasks, (v) => `${v.ref} (${v.count})`)} />
        <Meta label="configuration" value={fieldText(m.config, (v) => v.ref)} />
        <Meta label="protocol" value={fieldText(m.protocol, (v) => `${v.ref} (${v.basis})`)} />
        <Meta label="python" value={fieldText(m.python, (v) => `${v.implementation} ${v.version}`)} />
        <Meta label="platform" value={fieldText(m.platform, (v) => v.system)} />
        <Meta label="adapter" value={`${m.adapter?.adapter_module ?? "n/a"} · sdk ${fieldText(m.adapter?.sdk, (v) => `${v.package} ${v.version}`)}`} />
      </dl>
    </div>
  );
}

function CompareBox({ current, others }: { current: string; others: LiveExperiment[] }) {
  const [other, setOther] = React.useState("");
  const [result, setResult] = React.useState<Comparison | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  React.useEffect(() => {
    setResult(null);
    setError(null);
    if (!other) return;
    let alive = true;
    api.compare(current, other)
      .then((r) => alive && setResult(r.comparison))
      .catch((e: unknown) => alive && setError(String(e)));
    return () => {
      alive = false;
    };
  }, [current, other]);
  if (others.length === 0) {
    return <p className="font-mono text-sm text-[var(--color-ink-faint)]">No other recorded run to compare with.</p>;
  }
  return (
    <div className="space-y-3">
      <label className="block font-mono text-xs">
        Compare this run (A) with (B):{" "}
        <select value={other} onChange={(e) => setOther(e.target.value)} data-testid="compare-select"
          className="mt-1 block w-full max-w-full rounded border border-[var(--color-line)] bg-[var(--color-card)] px-2 py-1 sm:max-w-md">
          <option value="">choose a run</option>
          {others.map((o) => (
            <option key={o.experiment_id} value={o.experiment_id}>
              {(o.config.label ?? short(o.experiment_id))} · {clock(o.created_at)}
            </option>
          ))}
        </select>
      </label>
      {error && <p role="alert" className="font-mono text-xs text-[var(--color-brick)]">{error}</p>}
      {result && (
        <div data-testid="comparison">
          <p className="tnum font-mono text-sm">
            A {pct(result.a.rate)} ({result.a.passed}/{result.a.valid}) · B {pct(result.b.rate)} ({result.b.passed}/{result.b.valid}) ·
            difference A − B: {result.difference === null ? "undefined" : `${(result.difference * 100).toFixed(1)} points`}
            {result.interval && ` · ${Math.round(result.interval.confidence * 100)}% Newcombe-Wilson interval ${(result.interval.lower * 100).toFixed(1)} to ${(result.interval.upper * 100).toFixed(1)}`}
          </p>
          <p className="mt-1 flex flex-wrap gap-2">
            <Pill tone="neutral">descriptive comparison</Pill>
            {!result.interval && result.difference !== null && <Pill tone="warn">no interval shown</Pill>}
            {result.insufficient_sample && <Pill tone="warn">insufficient sample</Pill>}
            {result.recording_caveat && (
              <span data-testid="compare-recording-caveat">
                <Pill tone="warn">
                  recording not complete: {Object.keys(result.recording_caveat).map((k) => k.toUpperCase()).join(" and ")}
                </Pill>
              </span>
            )}
          </p>
          <ul className="mt-1 list-disc pl-5 text-xs text-[var(--color-ink-soft)]">
            {result.notes.map((n) => <li key={n}>{n}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

export function LiveRunPage() {
  const { id = "" } = useParams();
  const feed = useLiveFeed();
  const detail = useLive(() => api.experiment(id), feed.tick, id);
  const list = useLive(api.experiments, feed.tick);
  const d = detail.data;
  const myEvents = feed.events.filter((e) => e.experiment_id === id);

  return (
    <LabShell>
      <div className="space-y-10">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Link to="/live" className="font-mono text-xs uppercase tracking-wider underline">← all live runs</Link>
          <ConnectionBadge connection={detail.error && !d ? "offline" : feed.connection} />
        </div>

        {detail.loading && !d && <p className="font-mono text-sm" role="status">Loading run…</p>}
        {detail.error && !d && (detail.error.status === 404
          ? <p role="alert" className="card-flat p-6 font-mono text-sm" data-testid="unknown-run">No recorded run has this identifier.</p>
          : <Offline what="This run" />)}

        {d && (
          <>
            <header>
              <p className="font-mono text-xs uppercase tracking-[0.25em] text-[var(--color-signal)]">
                live backend · {d.experiment.config.provider ?? "provider n/a"} · {d.experiment.config.model ?? "model n/a"}
              </p>
              <h2 className="font-display text-3xl font-bold tracking-tight sm:text-5xl">
                {d.experiment.config.label ?? "Run"}
              </h2>
              <p className="mt-2 flex flex-wrap items-center gap-2">
                <StatusPill exp={d.experiment} /><KindBadge exp={d.experiment} /><RecordingBadge exp={d.experiment} />
                <span className="font-mono text-xs text-[var(--color-ink-faint)]">{d.experiment.experiment_id}</span>
              </p>
              <div className="mt-3"><RecordingNotice exp={d.experiment} /></div>
              {d.experiment.kind === "synthetic" && (
                <p className="card-flat mt-3 p-3 text-sm" data-testid="synthetic-banner" role="note">
                  <strong>Synthetic plumbing run.</strong> A built-in offline model was replayed through the runner.
                  No provider was called. The numbers below test the pipeline and are not experimental results.
                </p>
              )}
            </header>

            <section aria-label="Progress">
              <SectionTitle index="01" title="Progress" />
              <div className="card space-y-3 p-5">
                <ProgressBar exp={d.experiment} />
                <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                  <Meta label="current task" value={d.experiment.current_task ?? (d.experiment.status === "running" ? "between trials" : "none")} />
                  <Meta label="elapsed" value={duration(d.experiment.elapsed_s)} />
                  <Meta label="started" value={clock(d.experiment.started_at)} />
                  <Meta label="finished" value={clock(d.experiment.finished_at)} />
                </dl>
                {d.experiment.stale && <p role="alert" className="text-sm text-[var(--color-amber-deep)]">{d.experiment.stale_reason}</p>}
                {d.experiment.error && (
                  <p role="alert" className="font-mono text-sm text-[var(--color-brick)]" data-testid="run-error">
                    Run failed: {d.experiment.error.error_type}. Trials finished before the failure are kept below.
                  </p>
                )}
              </div>
            </section>

            <section aria-label="Reliability">
              <SectionTitle index="02" title="Reliability" hint="valid trials only" />
              <div className="card grid gap-6 p-5 sm:grid-cols-2">
                <RateLine block={d.analytics.pass_rate} label="pass rate" caveat={d.analytics.recording_caveat} />
                <dl className="grid grid-cols-2 gap-4 self-start">
                  <Meta label="trials finished" value={String(d.analytics.counts.trials_finished)} />
                  <Meta label="valid" value={String(d.analytics.counts.valid)} tip="Only valid trials enter a pass rate." />
                  <Meta label="excluded" value={String(d.analytics.counts.excluded)}
                    tip="Provider, environment or evaluator errors. Counted, never scored." />
                  <Meta label="tasks" value={String(d.analytics.counts.tasks)} />
                  <Meta label="tool calls executed" value={String(d.analytics.tool_calls_total)} />
                  <Meta label="model responses" value={String(d.analytics.model_requests_total)} />
                </dl>
              </div>
            </section>

            <section aria-label="Failure analysis">
              <SectionTitle index="03" title="Failure analysis" hint="valid failed trials" />
              <div className="card grid gap-6 p-5 sm:grid-cols-2">
                <div>
                  <h3 className="mb-2 font-mono text-xs uppercase tracking-wider">failure category</h3>
                  <Bars data={d.analytics.failure_distribution} empty="No failed valid trial." />
                </div>
                <div>
                  <h3 className="mb-2 font-mono text-xs uppercase tracking-wider">evaluator check failed</h3>
                  <Bars data={d.analytics.reason_categories} empty="No evaluator check failed." />
                </div>
              </div>
            </section>

            <section aria-label="Tasks">
              <SectionTitle index="04" title="Tasks" />
              <div className="card space-y-4 p-5">
                <TaskTable analytics={d.analytics} />
                {!d.analytics.by_depth.available && <NotAvailable what="by depth" info={d.analytics.by_depth} />}
                {!d.analytics.by_family.available && <NotAvailable what="by family" info={d.analytics.by_family} />}
              </div>
            </section>

            <section aria-label="Trials">
              <SectionTitle index="05" title="Trials" hint="names and outcomes only" />
              <div className="card p-5"><TrialTable trials={d.trials} /></div>
            </section>

            <section aria-label="Comparison">
              <SectionTitle index="06" title="Compare runs" hint="descriptive" />
              <div className="card p-5">
                <CompareBox current={id} others={(list.data?.experiments ?? []).filter((e) => e.experiment_id !== id)} />
              </div>
            </section>

            <section aria-label="Configuration and provenance">
              <SectionTitle index="07" title="Configuration and provenance" />
              <div className="card mb-4 p-5"><SourceProvenance exp={d.experiment} /></div>
              <dl className="card grid grid-cols-2 gap-4 p-5 sm:grid-cols-4">
                <Meta label="framework" value={d.experiment.config.framework} />
                <Meta label="execution mode" value={d.experiment.config.exec_mode} />
                <Meta label="temperature" value={String(d.experiment.config.temperature ?? "n/a")} />
                <Meta label="seed" value={String(d.experiment.config.seed ?? "n/a")} />
                <Meta label="repeats" value={String(d.experiment.config.repeats ?? "n/a")} />
                <Meta label="task schema" value={d.experiment.config.task_schema_version ?? "n/a"} />
                <Meta label="code revision" value={d.experiment.config.code_revision ? d.experiment.config.code_revision.slice(0, 10) : "not recorded"} />
                <Meta label="protocol" value={d.experiment.config.protocol_ref ?? "not recorded"} />
                <Meta label="provenance: verified" value={String(d.analytics.provenance_completeness.verified ?? 0)}
                  tip="Fields the record establishes, summed over finished trials." />
                <Meta label="indeterminate" value={String(d.analytics.provenance_completeness.indeterminate ?? 0)}
                  tip="For example calls emitted by the model: adapter capture is unverified, so only a lower bound is known." />
                <Meta label="not observable" value={String(d.analytics.provenance_completeness.not_observable ?? 0)}
                  tip="For example SDK retries: not visible to the harness and not assumed to be zero." />
              </dl>
            </section>

            <section aria-label="Run events">
              <SectionTitle index="08" title="Events" hint="this browser session" />
              <div className="card p-5"><EventList events={myEvents} /></div>
            </section>
          </>
        )}
      </div>
    </LabShell>
  );
}
