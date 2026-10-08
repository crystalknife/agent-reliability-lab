import * as React from "react";
import { Link } from "react-router-dom";
import { Meta, Reveal, SectionTitle } from "./display";
import { loadExperiment, loadManifest } from "../lib/load";
import { agentReliability, formatPct, validRows } from "../lib/selectors";
import type { ExperimentBundle, ManifestEntry } from "../types/domain";

interface ArmFacts {
  key: string;
  title: string;
  subtitle: string;
  linkId: string | null;
  valid: number;
  passed: number;
  failed: number;
  passingTasks: string[] | null;
}

function bundleFacts(key: string, title: string, subtitle: string, bundle: ExperimentBundle): ArmFacts {
  const valid = validRows(bundle.rows);
  const passed = valid.filter((r) => r.passed).length;
  const byTask: Record<string, { p: number; n: number }> = {};
  for (const r of bundle.rows) {
    const g = (byTask[r.task_id] ??= { p: 0, n: 0 });
    g.n += 1;
    if (r.passed) g.p += 1;
  }
  return {
    key, title, subtitle, linkId: bundle.manifest.id,
    valid: valid.length, passed, failed: valid.length - passed,
    passingTasks: Object.keys(byTask).sort().filter((t) => byTask[t].p === byTask[t].n && byTask[t].p > 0),
  };
}

/** Interpretation is derived, never hardcoded: each arm is labeled by how
 *  its observed rate relates to the baseline and unadjusted reference arms. */
function interpret(f: ArmFacts, baseRate: number, unadjustedRate: number): string {
  if (f.key === "baseline") return "Reference";
  const rate = f.valid === 0 ? NaN : f.passed / f.valid;
  if (rate === baseRate) return "Controlled — matches baseline";
  if (rate === unadjustedRate) return "Matches unadjusted arm";
  return "Differs from both references";
}

function ArmTable({ arms, baseRate, unadjustedRate }: {
  arms: ArmFacts[]; baseRate: number; unadjustedRate: number;
}) {
  return (
    <div className="card overflow-x-auto p-5">
      <table className="w-full min-w-[560px] text-left font-mono text-sm">
        <thead>
          <tr className="text-[11px] uppercase tracking-wider text-[var(--color-ink-faint)]">
            <th className="py-2 pr-3">Arm</th>
            <th className="py-2 pr-3 text-right">Valid</th>
            <th className="py-2 pr-3 text-right">Passed</th>
            <th className="py-2 pr-3 text-right">Failed</th>
            <th className="py-2 pr-3 text-right">Pass rate</th>
            <th className="py-2">Interpretation</th>
          </tr>
        </thead>
        <tbody>
          {arms.map((a) => (
            <tr key={a.key} className="border-t border-[var(--color-line)]">
              <td className="py-2 pr-3">
                {a.linkId ? (
                  <Link to={`/experiments/${a.linkId}`} className="font-bold underline hover:text-[var(--color-signal)]">
                    {a.title}
                  </Link>
                ) : (
                  <span className="font-bold">{a.title}</span>
                )}
                <span className="block text-[11px] font-normal text-[var(--color-ink-faint)]">{a.subtitle}</span>
              </td>
              <td className="tnum py-2 pr-3 text-right">{a.valid}</td>
              <td className="tnum py-2 pr-3 text-right">{a.passed}</td>
              <td className="tnum py-2 pr-3 text-right">{a.failed}</td>
              <td className="tnum py-2 pr-3 text-right font-bold">
                {a.valid === 0 ? "—" : formatPct(a.passed / a.valid)}
              </td>
              <td className="py-2 text-xs">{interpret(a, baseRate, unadjustedRate)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PassingSets({ arms }: { arms: ArmFacts[] }) {
  return (
    <div className="card space-y-3 p-5">
      {arms.map((a) => (
        <div key={a.key}>
          <p className="font-mono text-xs font-bold">{a.title}</p>
          {a.passingTasks === null ? (
            <p className="mt-1 font-mono text-xs text-[var(--color-ink-faint)]">
              per-task breakdown unavailable — summary-only arm
            </p>
          ) : a.passingTasks.length === 0 ? (
            <p className="mt-1 font-mono text-xs text-[var(--color-ink-faint)]">no passing tasks</p>
          ) : (
            <p className="mt-1 font-mono text-xs">
              {a.passingTasks.map((t) => (
                <span key={t} className="mr-2 inline-block rounded bg-[var(--color-paper-deep)] px-2 py-0.5">{t}</span>
              ))}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

interface Loaded {
  baseline: ExperimentBundle;
  unadjusted: ExperimentBundle;
  chatter: ExperimentBundle;
  parity: ExperimentBundle;
  combined: ExperimentBundle;
}

/** /experiments/exp-002 — bespoke pilot report. All figures are derived
 *  from the loaded trial bundles; prose stays qualitative and points at
 *  the full pilot document for the audited numbers. */
export function Exp002Page({ entry }: { entry: ManifestEntry }) {
  const [data, setData] = React.useState<Loaded | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  React.useEffect(() => {
    let live = true;
    loadManifest()
      .then(async (entries) => {
        const find = (id: string) => {
          const e = entries.find((x) => x.id === id);
          if (!e) throw new Error(`experiment ${id} not exported — run \`npm run data\``);
          return e;
        };
        const [unadjusted, baseline, chatter, parity, combined] = await Promise.all([
          loadExperiment(find("exp-002")),
          loadExperiment(find("exp-001")),
          loadExperiment(find("exp-002a")),
          loadExperiment(find("exp-002b")),
          loadExperiment(find("exp-002c")),
        ]);
        if (live) setData({ unadjusted, baseline, chatter, parity, combined });
      })
      .catch((e: unknown) => {
        if (live) setError(String(e));
      });
    return () => {
      live = false;
    };
  }, [entry]);
  if (error) {
    return <p className="card-flat p-6 font-mono text-sm text-[var(--color-brick)]">Could not load: {error}</p>;
  }
  if (!data) {
    return <p className="card-flat animate-pulse p-6 font-mono text-sm">Loading framework pilot…</p>;
  }
  const baseRel = agentReliability(data.baseline.rows);
  const baseRate = baseRel.n === 0 ? NaN : baseRel.success_rate;
  const full: ArmFacts[] = [
    bundleFacts("baseline", "Custom baseline", "EXP-001 · custom harness", data.baseline),
    bundleFacts("unadjusted", "LangChain unadjusted", "EXP-002 · full trials", data.unadjusted),
    bundleFacts("chatter", "Chatter stripped", "EXP-002A · full trials", data.chatter),
    bundleFacts("parity", "Schema parity", "EXP-002B · full trials", data.parity),
    bundleFacts("combined", "Schema + chatter", "EXP-002C · full trials", data.combined),
  ];
  const unadjustedRate = full[1].valid === 0 ? NaN : full[1].passed / full[1].valid;
  const seeds = [...new Set(data.baseline.rows.map((r) => r.seed))].sort((a, b) => a - b);
  return (
    <div className="space-y-10">
      <Reveal>
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.25em] text-[var(--color-signal)]">
            {entry.id} · framework pilot · observational
          </p>
          <h2 className="font-display text-4xl font-bold tracking-tight sm:text-6xl">
            {entry.name}
          </h2>
          <p className="mt-2 font-mono text-xs text-[var(--color-ink-faint)]">
            {entry.model} · seeds {seeds.join(", ")} · {data.baseline.rows.length} trials per arm
          </p>
          {entry.note && (
            <p className="mt-2 max-w-3xl text-sm leading-relaxed text-[var(--color-ink-soft)]">{entry.note}</p>
          )}
        </div>
      </Reveal>

      <Reveal>
        <section aria-label="Objective">
          <SectionTitle index="01" title="Objective" />
          <div className="card p-5">
            <p className="max-w-3xl leading-relaxed">
              EXP-002 ran the MiniBank suite through a LangChain tool-calling agent
              under the same model, environment, tasks, and deterministic evaluator
              as the EXP-001 baseline. Forensic controls then isolated two
              model-facing adapter differences. This page reports all arms as
              recorded; the unadjusted result is retained as an observational
              arm, not as evidence of framework superiority.
            </p>
            <dl className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
              <Meta label="model" value={entry.model} />
              <Meta label="provider" value={entry.provider} />
              <Meta label="temperature" value={String(data.unadjusted.rows[0]?.temperature ?? "?")} />
              <Meta label="seeds" value={seeds.join(", ")} />
            </dl>
          </div>
        </section>
      </Reveal>

      <section aria-label="Five-arm comparison">
        <SectionTitle index="02" title="Five-arm comparison" hint="observed rates" />
        <ArmTable arms={full} baseRate={baseRate} unadjustedRate={unadjustedRate} />
        <p className="mt-2 font-mono text-[11px] text-[var(--color-ink-faint)]">
          Every figure above is computed from the loaded trial rows; drill into
          any full-trial arm for per-task and per-trial inspection.
        </p>
      </section>

      <section aria-label="Passing task sets">
        <SectionTitle index="03" title="Passing task sets" />
        <PassingSets arms={full} />
      </section>

      <section aria-label="Findings">
        <SectionTitle index="04" title="Findings" />
        <div className="card-flat space-y-3 p-5 leading-relaxed">
          <p>
            <strong>Schema parity.</strong> Equating the model-facing tool schemas
            with the harness rendering moved the observed arm to the baseline
            level (see comparison). The audit treats schema decoration as a
            material confound in the unadjusted result; causality at this sample
            size is not claimed.
          </p>
          <p>
            <strong>Chatter.</strong> Stripping retained assistant text left the
            observed arm unchanged, so conversation-text retention is the less
            likely explanation. Note the combined run contained no chattery
            messages, so that control ran unexercised.
          </p>
          <p>
            <strong>Parallel calls.</strong> The harness executes one tool call per
            model response; LangChain executes whole batches. Batches appeared
            only on the horizon task and every observed batch failed — a medium
            protocol confounder with no pass attribution.
          </p>
          <a href="https://github.com/crystalknife/agent-reliability-lab/blob/main/docs/experiments/EXP-002-langchain-framework-pilot.md"
            target="_blank" rel="noreferrer"
            className="inline-block font-mono text-sm underline hover:text-[var(--color-signal)]">
            View full pilot report (EXP-002) →
          </a>
        </div>
      </section>

      <section aria-label="Framework equivalence">
        <SectionTitle index="05" title="Framework equivalence" />
        <div className="card-flat space-y-3 p-5 leading-relaxed">
          <p>
            The lab separates agent behavior, framework behavior,
            adapter/model-interface effects, and infrastructure faults. A
            framework comparison is only meaningful when prompts, schemas, and
            orchestration are model-facing equivalent.
          </p>
          <p>
            EXP-002 demonstrated why: the initial LangChain observation changed
            once schema parity was enforced (see comparison above). Until
            equivalence holds, differing results describe the adapters, not
            necessarily the frameworks.
          </p>
        </div>
      </section>

      <section aria-label="Limitations">
        <SectionTitle index="06" title="Limitations" />
        <div className="card-flat p-5 leading-relaxed">
          <ul className="list-disc space-y-1 pl-5">
            <li>Small samples: {data.baseline.rows.length} trials per arm — no significance claims.</li>
            <li>Parallel-call, malformed-argument, unknown-tool, and budget semantics differ by design.</li>
            <li>The chatter control ran unexercised in the combined arm.</li>
          </ul>
        </div>
      </section>

      <section aria-label="Next experiment">
        <SectionTitle index="07" title="Next: EXP-003" />
        <div className="card p-5 leading-relaxed">
          <p className="max-w-3xl">
            Controlled replication — custom harness vs LangChain with schema
            parity, 8 tasks × 5 repeats (40 trials per arm). Not yet run.
          </p>
        </div>
      </section>
    </div>
  );
}
