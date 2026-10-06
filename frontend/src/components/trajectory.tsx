import { Link } from "react-router-dom";
import { prettyJson, shortId } from "../lib/format";
import type { Trajectory, TrajStep, TrialRow } from "../types/domain";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "./anim/collapsible";
import { Reveal } from "./display";
import { FailurePill, PassPill, StatusPill } from "./display";

function ObsJson({ value }: { value: unknown }) {
  return (
    <pre className="overflow-x-auto whitespace-pre-wrap break-words bg-[var(--color-ink)] p-3 font-mono text-[11px] leading-relaxed text-[var(--color-card)]">
      {prettyJson(value)}
    </pre>
  );
}

function ArgsLine({ args }: { args: Record<string, unknown> }) {
  const entries = Object.entries(args);
  if (entries.length === 0) return <span className="font-mono text-xs text-[var(--color-ink-faint)]">no arguments</span>;
  return (
    <span className="font-mono text-xs">
      {entries.map(([k, v]) => (
        <span key={k} className="mr-3">
          <span className="text-[var(--color-ink-faint)]">{k}=</span>
          <span className="font-bold">{JSON.stringify(v)}</span>
        </span>
      ))}
    </span>
  );
}

const KIND_STYLE: Record<string, string> = {
  MODEL: "bg-[var(--color-ink)] text-[var(--color-card)]",
  TOOL: "bg-[var(--color-signal)] text-[var(--color-card)]",
  OBSERVATION: "bg-[var(--color-paper-deep)] text-[var(--color-ink)]",
};

function StepEvent({ index, step }: { index: number; step: TrajStep }) {
  // Terminal notes (model_error / invalid_action) carry an error observation,
  // not a tool result; every other step is MODEL decides → TOOL executes.
  const isNote = step.action === "model_error" || step.action === "invalid_action";
  return (
    <Collapsible>
      <div className="card-flat overflow-hidden">
        <CollapsibleTrigger className="flex w-full items-center gap-3 p-3 text-left hover:bg-[var(--color-paper-deep)]">
          <span className="tnum font-mono text-xs font-bold text-[var(--color-ink-faint)]">
            {String(index + 1).padStart(2, "0")}
          </span>
          <span className={`rounded px-2 py-0.5 font-mono text-[11px] font-bold uppercase tracking-wider ${isNote ? KIND_STYLE.OBSERVATION : KIND_STYLE.TOOL}`}>
            {isNote ? "note" : "tool"}
          </span>
          <span className="truncate font-mono text-sm font-bold">{step.action}</span>
          <span className="ml-auto hidden truncate text-right sm:block">
            <ArgsLine args={step.arguments} />
          </span>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="space-y-2 border-t border-[var(--color-line)] p-3">
            <div className="sm:hidden">
              <ArgsLine args={step.arguments} />
            </div>
            <p className="font-mono text-[11px] uppercase tracking-wider text-[var(--color-ink-faint)]">observation</p>
            <ObsJson value={step.observation} />
          </div>
        </CollapsibleContent>
      </div>
    </Collapsible>
  );
}

export function StepTimeline({ traj }: { traj: Trajectory }) {
  return (
    <ol className="space-y-3">
      {traj.steps.map((s, i) => (
        <li key={s.step}>
          <Reveal delay={Math.min(i * 0.04, 0.3)}>
            <StepEvent index={i} step={s} />
          </Reveal>
        </li>
      ))}
    </ol>
  );
}

export function FinalCard({ traj }: { traj: Trajectory }) {
  return (
    <div className="card p-5">
      <p className="mb-2 font-mono text-[11px] uppercase tracking-wider text-[var(--color-ink-faint)]">final answer</p>
      <p className="text-lg leading-relaxed">
        {typeof traj.final_answer === "string" && traj.final_answer.trim()
          ? traj.final_answer
          : <span className="font-mono text-sm text-[var(--color-ink-faint)]">(no final answer — {traj.termination_reason})</span>}
      </p>
    </div>
  );
}

export function VerdictPanel({ row }: { row: TrialRow }) {
  return (
    <div className="card p-5">
      <p className="mb-3 font-mono text-[11px] uppercase tracking-wider text-[var(--color-ink-faint)]">evaluator verdict</p>
      <div className="mb-3 flex flex-wrap gap-2">
        <PassPill passed={row.passed} />
        <FailurePill failure={row.failure} />
        <StatusPill status={row.trial_status} />
      </div>
      {row.reasons.length > 0 ? (
        <ul className="space-y-1.5">
          {row.reasons.map((r, i) => (
            <li key={i} className="border-l-2 border-[var(--color-signal)] pl-3 font-mono text-xs leading-relaxed">
              {r}
            </li>
          ))}
        </ul>
      ) : (
        <p className="font-mono text-xs text-[var(--color-ink-faint)]">no reasons recorded — clean pass.</p>
      )}
      {row.provider_error && (
        <div className="mt-3">
          <p className="mb-1 font-mono text-[11px] uppercase tracking-wider text-[var(--color-ink-faint)]">raw provider error (preserved)</p>
          <ObsJson value={row.provider_error} />
        </div>
      )}
    </div>
  );
}

export function TrialLinkRow({ row, expId }: { row: TrialRow; expId: string }) {
  return (
    <Link
      to={`/experiments/${expId}/trials/${row.run_id}`}
      className="card-flat flex flex-wrap items-center gap-2 p-3 hover:bg-[var(--color-paper-deep)] sm:gap-3"
    >
      <span className="font-mono text-xs text-[var(--color-ink-faint)]">{shortId(row.run_id)}</span>
      <span className="min-w-0 break-words font-mono text-xs font-bold">{row.task_id}</span>
      <span className="ml-auto flex flex-wrap gap-1.5">
        <PassPill passed={row.passed} />
        <FailurePill failure={row.failure} />
      </span>
    </Link>
  );
}
