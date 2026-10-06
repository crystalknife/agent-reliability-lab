import * as React from "react";
import { Link } from "react-router-dom";
import type { ExperimentBundle } from "../types/domain";
import { agentReliability, FAILURE_ORDER, formatPct, rate, validRows } from "../lib/selectors";
import { dataUrl } from "../lib/load";
import { Meta, SectionTitle } from "./display";

// Published location of the full experiment report. Kept as one constant so
// a repo rename fixes every link; the UI never duplicates the document.
const FULL_REPORT_URL =
  "https://github.com/crystalknife/agent-reliability-lab/blob/main/docs/experiments/EXP-001-local-qwen3-baseline.md";

export interface TaskCatalogEntry {
  task_id: string;
  difficulty: string;
  family: string;
  prompt: string;
  customer_id: string;
  max_steps: number;
  required_tools: string[];
  required_evidence: string[];
  grounded_numbers: number[];
  expected_eligible: boolean;
  expected_rule: string;
  support: string | null;
  expected_answer: { type: string } | null;
}

export function useTaskCatalog() {
  const [catalog, setCatalog] = React.useState<TaskCatalogEntry[] | null>(null);
  React.useEffect(() => {
    fetch(dataUrl("tasks.json"))
      .then((r) => {
        if (!r.ok) throw new Error("tasks.json missing");
        return r.json();
      })
      .then((j) => {
        setCatalog(Array.isArray(j) ? (j as TaskCatalogEntry[]) : null);
      })
      .catch(() => setCatalog(null));
  }, []);
  return catalog;
}

function distinct<T>(xs: T[]): T[] {
  return [...new Set(xs)];
}

/** 01 — what exactly ran. Every value derived from the bundle, nothing hardcoded. */
export function ContextSection({ bundle }: { bundle: ExperimentBundle }) {
  const { manifest, rows, trajs } = bundle;
  const versions = distinct(Object.values(trajs).map((t) => String(t.model_version ?? "?")));
  const seeds = distinct(rows.map((r) => r.seed)).sort((a, b) => a - b);
  const perTask = distinct(rows.map((r) => r.task_id)).length;
  const temps = distinct(rows.map((r) => r.temperature));
  return (
    <section aria-label="Experiment context">
      <SectionTitle index="01" title="Experiment context" />
      <div className="card p-5">
        <p className="max-w-3xl leading-relaxed">
          Baseline evaluation of agent reliability using the MiniBank task suite:
          {" "}{rows.length} trials ({perTask} tasks × {rows.length / perTask} repetitions),
          each trial an independent ReAct episode scored by the deterministic evaluator.
        </p>
        <dl className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Meta label="model" value={manifest.model} />
          <Meta label="provider" value={manifest.provider} />
          <Meta label="runtime version" value={versions.join(", ")} tip="Recorded per-trajectory model_version." />
          <Meta label="temperature" value={temps.join(", ")} />
          <Meta label="seeds" value={seeds.join(", ")} />
          <Meta label="inference" value="llama.cpp, local" tip="External server; MiniLab never starts it." />
        </dl>
      </div>
    </section>
  );
}

/** 02 — how to read the numbers. Static principles from docs/architecture. */
export function MethodologySection({ bundle }: { bundle: ExperimentBundle }) {
  const rel = agentReliability(bundle.rows);
  return (
    <section aria-label="Methodology">
      <SectionTitle index="02" title="Methodology" />
      <div className="card-flat space-y-3 p-5 leading-relaxed">
        <p>
          <strong>Deterministic evaluator, no LLM judge.</strong> Each trajectory is
          checked against declared evidence: verdict wording, cited evidence,
          required tool calls, and numbers grounded in tool observations.
        </p>
        <p>
          <strong>Reliability = passed ÷ valid trials</strong> — currently{" "}
          <span className="tnum font-bold">{formatPct(rel.success_rate)}</span> over{" "}
          <span className="tnum font-bold">{rel.n}</span>. Provider and infrastructure
          faults are recorded as trial status, never as agent failures, and are
          excluded from this number.
        </p>
        <p>
          <strong>Failure taxonomy</strong> (the benchmark's fixed set — shown whether
          or not each occurred here):{" "}
          <span className="font-mono text-sm">{FAILURE_ORDER.join(" · ").replace(/_/g, " ")}</span>
        </p>
        <a href={FULL_REPORT_URL} target="_blank" rel="noreferrer"
          className="inline-block font-mono text-sm underline hover:text-[var(--color-signal)]">
          View full experiment report (EXP-001) →
        </a>
      </div>
    </section>
  );
}

/** 03 — what was tested, from the exported task catalog (tasks.py via exporter). */
export function TaskSuiteSection({ bundle }: { bundle: ExperimentBundle }) {
  const catalog = useTaskCatalog();
  const byTask: Record<string, typeof bundle.rows> = {};
  for (const r of bundle.rows) (byTask[r.task_id] ??= []).push(r);
  return (
    <section aria-label="Task suite">
      <SectionTitle index="03" title="Task suite" hint={`${Object.keys(byTask).length} tasks`} />
      <div className="grid gap-3 sm:grid-cols-2">
        {Object.keys(byTask).sort().map((tid) => {
          const g = byTask[tid];
          const r = rate(g.filter((x) => x.trial_status === "valid"));
          const cat = catalog?.find((c) => c.task_id === tid);
          return (
            <Link key={tid} to={`/experiments/${bundle.manifest.id}/tasks/${tid}`}
              className="card block p-4 hover:-translate-y-0.5">
              <div className="flex items-baseline justify-between gap-2">
                <p className="font-mono text-sm font-bold">{tid}</p>
                <p className="tnum font-mono text-sm font-bold">{formatPct(r.success_rate)}</p>
              </div>
              <p className="mt-1 font-mono text-xs uppercase tracking-wider text-[var(--color-signal)]">
                {cat?.family ?? g[0].difficulty} · {g[0].difficulty}
              </p>
              <p className="mt-2 text-sm leading-relaxed text-[var(--color-ink-soft)]">
                {cat ? cat.prompt : "MiniBank task."}
              </p>
              <p className="mt-2 font-mono text-[11px] text-[var(--color-ink-faint)]">
                tools: {(cat?.required_tools ?? []).join(", ") || "—"}
                {" · answer: "}{cat?.expected_answer ? cat.expected_answer.type : "verdict phrases"}
                {" · budget: "}{cat?.max_steps ?? g[0].max_steps} steps
              </p>
            </Link>
          );
        })}
      </div>
    </section>
  );
}

export function TrialLedgerLink({ bundle }: { bundle: ExperimentBundle }) {
  const valid = validRows(bundle.rows);
  return (
    <section aria-label="Trial ledger">
      <SectionTitle index="07" title="Trial ledger" hint={`${bundle.rows.length} runs`} />
      <Link to={`/experiments/${bundle.manifest.id}/trials`}
        className="card block p-5 hover:-translate-y-0.5">
        <p className="font-display text-xl font-bold">
          Inspect all {bundle.rows.length} trials <span aria-hidden="true">→</span>
        </p>
        <p className="tnum mt-1 font-mono text-xs text-[var(--color-ink-faint)]">
          {valid.filter((r) => r.passed).length} passed · {valid.filter((r) => !r.passed).length} failed ·{" "}
          {bundle.rows.length - valid.length} provider errors — filterable, deep-linkable.
        </p>
      </Link>
    </section>
  );
}
