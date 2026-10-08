import { Link, useParams, useSearchParams } from "react-router-dom";
import { useExperiment, useManifest } from "../hooks/useExperiment";
import { agentReliability } from "../lib/selectors";
import { LabShell } from "../layouts/LabShell";
import { DashboardBody } from "../components/Dashboard";
import { Exp002Page } from "../components/Exp002";
import { Empty, Reveal, SectionTitle } from "../components/display";
import { Tabs } from "../components/anim/primitives";
import { StepTimeline, FinalCard, TrialLinkRow, VerdictPanel } from "../components/trajectory";
import { FailurePill, Meta, PassPill, StatusPill } from "../components/display";
import { formatTime, shortId } from "../lib/format";

function Loading({ what }: { what: string }) {
  return <p className="card-flat animate-pulse p-6 font-mono text-sm">Loading {what}…</p>;
}

function Failed({ error }: { error: string }) {
  return <p className="card-flat p-6 font-mono text-sm text-[var(--color-brick)]">Could not load: {error}</p>;
}

/** / — latest experiment dashboard. */
export function DashboardPage() {
  const { entries, error } = useManifest();
  if (error) return <LabShell><Failed error={error} /></LabShell>;
  if (!entries) return <LabShell><Loading what="experiment index" /></LabShell>;
  if (entries.length === 0) return <LabShell><Empty what="experiments — run `npm run data` first" /></LabShell>;
  return <ExperimentPage id={entries[0].id} />;
}

/** /experiments/:id — dashboard scoped to one experiment. The pilot report
 *  has its own page; every other experiment uses the trial dashboard. */
export function ExperimentPage({ id: propId }: { id?: string }) {
  const params = useParams();
  const id = propId ?? params.id;
  const { bundle, error } = useExperiment(id);
  const { entries } = useManifest();
  if (id === "exp-002") {
    const pilot = entries?.find((e) => e.id === "exp-002");
    if (!pilot) return <LabShell><Loading what="experiment index" /></LabShell>;
    return (
      <LabShell>
        <Exp002Page entry={pilot} />
      </LabShell>
    );
  }
  if (error) return <LabShell><Failed error={error} /></LabShell>;
  if (!bundle) return <LabShell><Loading what={`experiment ${id}`} /></LabShell>;
  return (
    <LabShell>
      <DashboardBody bundle={bundle} />
    </LabShell>
  );
}

/** /experiments — artifact list. */
export function ExperimentsPage() {
  const { entries, error } = useManifest();
  return (
    <LabShell>
      <SectionTitle index="00" title="Experiments" hint={`${entries?.length ?? 0} artifacts`} />
      {error && <Failed error={error} />}
      {!entries && <Loading what="experiments" />}
      <div className="grid gap-4 sm:grid-cols-2">
        {entries?.map((e, i) => (
          <Reveal key={e.id} delay={Math.min(i * 0.05, 0.25)}>
            <Link to={`/experiments/${e.id}`} className="card flex h-full flex-col p-5 hover:-translate-y-0.5">
              <p className="font-mono text-xs text-[var(--color-signal)]">
                {e.id}
                {e.tag && (
                  <span className="ml-2 rounded bg-[var(--color-paper-deep)] px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-[var(--color-ink-soft)]">
                    {e.tag}
                  </span>
                )}
              </p>
              <h3 className="font-display text-2xl font-bold tracking-tight">{e.name}</h3>
              {e.note && (
                <p className="mt-1 text-sm leading-relaxed text-[var(--color-ink-soft)]">{e.note}</p>
              )}
              <p className="mt-auto pt-2 font-mono text-xs text-[var(--color-ink-soft)]">
                {e.provider} · {e.model} · <span className="tnum">{e.trials} trials</span> · {e.created}
              </p>
            </Link>
          </Reveal>
        ))}
      </div>
    </LabShell>
  );
}

/** /experiments/:id/tasks/:taskId — trials for one task. */
export function TaskPage() {
  const { id, taskId } = useParams();
  const { bundle, error } = useExperiment(id);
  if (error) return <LabShell><Failed error={error} /></LabShell>;
  if (!bundle) return <LabShell><Loading what="task" /></LabShell>;
  const trials = bundle.rows.filter((r) => r.task_id === taskId);
  const rel = agentReliability(trials);
  return (
    <LabShell>
      <Link to={`/experiments/${id}`} className="font-mono text-xs text-[var(--color-ink-faint)] hover:text-[var(--color-ink)]">← {id}</Link>
      <h2 className="font-display mt-2 text-4xl font-bold tracking-tight">{taskId}</h2>
      <p className="tnum mt-1 font-mono text-xs text-[var(--color-ink-faint)]">
        {trials.length} trials · reliability {(rel.success_rate * 100).toFixed(0)}% over {rel.n} valid
      </p>
      <div className="mt-6 space-y-2">
        {trials.map((r) => <TrialLinkRow key={r.run_id} row={r} expId={id ?? ""} />)}
        {trials.length === 0 && <Empty what={`trials for ${taskId}`} />}
      </div>
    </LabShell>
  );
}

/** /experiments/:id/trials — filterable trial table.
 * Filters live in the query string (?tab=, ?difficulty=, ?failure=) so every
 * filtered view is deep-linkable, refresh-safe, and back-navigable. */
export function TrialsPage() {
  const { id } = useParams();
  const { bundle, error } = useExperiment(id);
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "all";
  const difficulty = params.get("difficulty");
  const failure = params.get("failure");
  if (error) return <LabShell><Failed error={error} /></LabShell>;
  if (!bundle) return <LabShell><Loading what="trials" /></LabShell>;
  const set = (k: string, v: string | null) => {
    const next = new URLSearchParams(params);
    if (v === null) next.delete(k);
    else next.set(k, v);
    setParams(next, { preventScrollReset: true });
  };
  const all = bundle.rows;
  const shown = all.filter((r) => {
    if (tab === "passed" && !(r.passed && r.trial_status === "valid")) return false;
    if (tab === "failed" && !(!r.passed && r.trial_status === "valid")) return false;
    if (tab === "provider_error" && r.trial_status !== "provider_error") return false;
    if (difficulty && r.difficulty !== difficulty) return false;
    if (failure && r.failure !== failure) return false;
    return true;
  });
  const tabs = [
    { id: "all", label: "all", count: all.length },
    { id: "passed", label: "passed", count: all.filter((r) => r.passed && r.trial_status === "valid").length },
    { id: "failed", label: "failed", count: all.filter((r) => !r.passed && r.trial_status === "valid").length },
    { id: "provider_error", label: "provider errors", count: all.filter((r) => r.trial_status === "provider_error").length },
  ];
  return (
    <LabShell>
      <Link to={`/experiments/${id}`} className="font-mono text-xs text-[var(--color-ink-faint)] hover:text-[var(--color-ink)]">← {id}</Link>
      <h2 className="font-display mt-2 text-4xl font-bold tracking-tight">Trials</h2>
      <div className="mt-4"><Tabs tabs={tabs} value={tab} onChange={(t) => set("tab", t === "all" ? null : t)} label="Filter trials" /></div>
      {(difficulty || failure) && (
        <div className="mt-3 flex flex-wrap items-center gap-2 font-mono text-xs">
          <span className="text-[var(--color-ink-faint)]">filtered:</span>
          {difficulty && <FilterChip label={`difficulty: ${difficulty}`} onClear={() => set("difficulty", null)} />}
          {failure && <FilterChip label={`failure: ${failure.replace(/_/g, " ")}`} onClear={() => set("failure", null)} />}
          <button onClick={() => setParams({}, { preventScrollReset: true })}
            className="underline hover:text-[var(--color-signal)]">clear all</button>
        </div>
      )}
      <div className="mt-4 space-y-2">
        {shown.map((r) => <TrialLinkRow key={r.run_id} row={r} expId={id ?? ""} />)}
        {shown.length === 0 && <Empty what="trials under this filter" />}
      </div>
    </LabShell>
  );
}

function FilterChip({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <button onClick={onClear} aria-label={`Clear filter ${label}`}
      className="card-flat px-2.5 py-1.5 font-mono text-xs hover:bg-[var(--color-paper-deep)]">
      {label} ✕
    </button>
  );
}

/** /experiments/:id/trials/:runId — full trajectory inspection. */
export function TrialPage() {
  const { id, runId } = useParams();
  const { bundle, error } = useExperiment(id);
  if (error) return <LabShell><Failed error={error} /></LabShell>;
  if (!bundle) return <LabShell><Loading what="trial" /></LabShell>;
  const row = bundle.rows.find((r) => r.run_id === runId);
  const traj = runId ? bundle.trajs[runId] : undefined;
  if (!row || !traj) return <LabShell><Empty what={`trial ${shortId(runId ?? "")}`} /></LabShell>;
  return (
    <LabShell>
      <Link to={`/experiments/${id}/trials`} className="font-mono text-xs text-[var(--color-ink-faint)] hover:text-[var(--color-ink)]">← all trials</Link>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2">
        <h2 className="font-display min-w-0 break-words text-3xl font-bold tracking-tight sm:text-4xl">{row.task_id}</h2>
        <span className="flex flex-wrap items-center gap-2">
          <PassPill passed={row.passed} />
          <FailurePill failure={row.failure} />
          <StatusPill status={row.trial_status} />
        </span>
      </div>
      <p className="mt-1 font-mono text-xs text-[var(--color-ink-faint)]">
        run {shortId(row.run_id, 12)} · {row.human_run_id} · seed {row.seed} · {row.model} · {formatTime(row.timestamp)}
      </p>
      <div className="mt-6 grid gap-6 lg:grid-cols-5">
        <div className="space-y-3 lg:col-span-3">
          <StepTimeline traj={traj} />
          <FinalCard traj={traj} />
        </div>
        <div className="space-y-4 lg:col-span-2">
          <VerdictPanel row={row} />
          <div className="card-flat p-5">
            <p className="mb-3 font-mono text-[11px] uppercase tracking-wider text-[var(--color-ink-faint)]">provenance</p>
            <dl className="grid grid-cols-2 gap-3">
              <Meta label="provider" value={traj.provider} />
              <Meta label="model" value={traj.model} />
              {row.framework && <Meta label="framework" value={row.framework} />}
              {row.strategy && <Meta label="strategy" value={row.strategy} />}
              <Meta label="temperature" value={String(traj.temperature)} />
              <Meta label="max steps" value={`${traj.steps.length} / ${traj.max_steps}`} />
              <Meta label="termination" value={traj.termination_reason} />
              <Meta label="prompt hash" value={shortId(traj.prompt_hash, 12)} tip={traj.prompt_hash} />
            </dl>
          </div>
        </div>
      </div>
    </LabShell>
  );
}
