import { Link } from "react-router-dom";
import type { ExperimentBundle } from "../types/domain";
import {
  agentReliability, failureHistogram, FAILURE_ORDER, formatPct,
  groupBy, providerErrors, rate, statusCounts, validRows,
} from "../lib/selectors";
import { formatTime } from "../lib/format";
import { Tip } from "./anim/primitives";
import { BarRow, Empty, Meta, ReliabilityDial, Reveal, SectionTitle } from "./display";
import { ContextSection, MethodologySection, TaskSuiteSection, TrialLedgerLink } from "./Context";

export function DashboardBody({ bundle }: { bundle: ExperimentBundle }) {
  const { manifest, rows } = bundle;
  const valid = validRows(rows);
  const rel = agentReliability(rows);
  const hist = failureHistogram(rows);
  const maxHist = Math.max(0, ...Object.values(hist));
  const byTask = groupBy(rows, (r) => r.task_id) as Record<string, typeof rows>;
  const byDiff = groupBy(rows, (r) => r.difficulty) as Record<string, typeof rows>;

  return (
    <div className="space-y-10">
      <Reveal>
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.25em] text-[var(--color-signal)]">
            {manifest.id} · {manifest.provider} · {manifest.model}
          </p>
          <h2 className="font-display text-4xl font-bold tracking-tight sm:text-6xl">
            {manifest.name}
          </h2>
          <p className="mt-2 font-mono text-xs text-[var(--color-ink-faint)]">
            {rows.length} trials · {manifest.created}
          </p>
        </div>
      </Reveal>

      <Reveal>
        <div className="card grid gap-6 p-6 sm:grid-cols-2">
          <ReliabilityDial value={rel.success_rate} n={valid.length} label="agent reliability" />
          <dl className="grid grid-cols-2 gap-4 self-center">
            <Meta label="valid trials" value={String(valid.length)} tip="Trials with usable evidence; provider faults excluded." />
            <Meta label="passed" value={String(valid.filter((r) => r.passed).length)} />
            <Meta label="failed" value={String(valid.filter((r) => !r.passed).length)} />
            <Meta label="provider errors" value={String(providerErrors(rows).length)} tip="Infrastructure faults — never charged to the agent." />
          </dl>
        </div>
      </Reveal>

      <ContextSection bundle={bundle} />
      <MethodologySection bundle={bundle} />
      <TaskSuiteSection bundle={bundle} />

      <section aria-label="Failure distribution">
        <SectionTitle index="04" title="Failure distribution" hint="valid trials only" />
        <div className="card space-y-2 p-5">
          {FAILURE_ORDER.filter((f) => hist[f]).map((f) => (
            <BarRow key={f} label={f} count={hist[f]} max={maxHist}
              to={`/experiments/${manifest.id}/trials?failure=${f}`} />
          ))}
          {Object.keys(hist).length === 0 && <Empty what="failures" />}
        </div>
      </section>

      <section aria-label="Task performance">
        <SectionTitle index="05" title="Task performance" />
        <div className="card space-y-2 p-5">
          {Object.keys(byTask).sort().map((tid) => {
            const g = byTask[tid];
            const r = rate(g.filter((x) => x.trial_status === "valid"));
            return (
              <Link key={tid} to={`/experiments/${manifest.id}/tasks/${tid}`}
                className="flex flex-col gap-1.5 rounded px-1 py-1.5 hover:bg-[var(--color-paper-deep)] sm:flex-row sm:items-center sm:gap-3">
                <span className="min-w-0 truncate font-mono text-xs font-bold sm:w-48 sm:shrink-0">{tid}</span>
                <span className="hidden font-mono text-xs text-[var(--color-ink-faint)] sm:inline">{g[0].difficulty}</span>
                <div className="flex items-baseline justify-between gap-2 sm:contents">
                  <span className="font-mono text-xs text-[var(--color-ink-faint)] sm:hidden">{g[0].difficulty}</span>
                  <Tip text={`${g.filter((x) => x.passed).length} of ${r.n} valid trials passed`}>
                    <span className="tnum font-mono text-xs font-bold sm:w-24 sm:text-right">
                      {formatPct(r.success_rate)} · {r.n}
                    </span>
                  </Tip>
                </div>
                <div className="h-5 w-full border border-[var(--color-line)] bg-[var(--color-paper)] sm:flex-1">
                  <div className="h-full bg-[var(--color-ink)]" style={{ width: `${r.success_rate * 100}%` }} />
                </div>
              </Link>
            );
          })}
        </div>
      </section>

      <section aria-label="Difficulty performance">
        <SectionTitle index="06" title="Difficulty" />
        <div className="grid gap-3 sm:grid-cols-3">
          {Object.keys(byDiff).sort().map((d) => {
            const g = byDiff[d];
            const r = rate(g.filter((x) => x.trial_status === "valid"));
            return (
              <Link key={d} to={`/experiments/${manifest.id}/trials?difficulty=${d}`}
                className="card block p-4 hover:-translate-y-0.5">
                <p className="font-mono text-xs uppercase tracking-wider text-[var(--color-ink-faint)]">{d}</p>
                <p className="tnum font-display text-3xl font-bold">{formatPct(r.success_rate)}</p>
                <p className="tnum font-mono text-xs text-[var(--color-ink-faint)]">{r.n} valid trials →</p>
              </Link>
            );
          })}
        </div>
      </section>

      <TrialLedgerLink bundle={bundle} />

      <section aria-label="Experiment metadata">
        <SectionTitle index="08" title="Metadata" />
        <div className="card-flat p-5">
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Meta label="experiment" value={manifest.id} />
            <Meta label="model" value={manifest.model} />
            <Meta label="provider" value={manifest.provider} />
            <Meta label="status" value={JSON.stringify(statusCounts(rows))} />
            <Meta label="first run" value={formatTime(Math.min(...rows.map((r) => r.timestamp)))} />
          </dl>
        </div>
      </section>
    </div>
  );
}
