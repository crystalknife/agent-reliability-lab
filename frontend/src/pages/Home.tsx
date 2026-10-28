import * as React from "react";
import { Link } from "react-router-dom";
import { useManifest } from "../hooks/useExperiment";
import { dataUrl } from "../lib/load";
import { isTrialRow } from "../lib/guards";
import { validRows } from "../lib/selectors";
import { Reveal } from "../components/display";
import { HeroChase } from "../components/HeroChase";
import { LabShell } from "../layouts/LabShell";

/** Small orange line icons in the Observatory style (decorative). */
function Icon({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <svg width="44" height="44" viewBox="0 0 44 44" fill="none" role="img" aria-label={label}>
      <g stroke="var(--color-signal)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        {children}
      </g>
    </svg>
  );
}

function HomeSection({ title, children }: {
  title: string; children: React.ReactNode;
}) {
  return (
    <section aria-label={title} className="mt-10">
      <div className="mb-4">
        <h2 className="font-display text-xl font-bold tracking-tight sm:text-2xl">{title}</h2>
      </div>
      {children}
    </section>
  );
}

const WORK_AREAS = [
  {
    icon: "bar chart",
    title: "Reproducible experiments",
    body: "Controlled runs, clear task suites, and transparent methods. All experiments, code, and analysis are public so others can inspect and reproduce the results.",
    art: (
      <>
        <rect x="6" y="18" width="8" height="20" fill="var(--color-signal)" stroke="none" />
        <rect x="18" y="10" width="8" height="28" fill="var(--color-signal)" stroke="none" />
        <rect x="30" y="22" width="8" height="16" fill="var(--color-paper-deep)" />
      </>
    ),
  },
  {
    icon: "document",
    title: "Failure analysis",
    body: "Understand where agent workflows break and why. We inspect trajectories, categorize failure modes, and analyze patterns across tasks and environments.",
    art: (
      <>
        <rect x="10" y="6" width="24" height="32" />
        <line x1="15" y1="15" x2="29" y2="15" />
        <line x1="15" y1="21" x2="29" y2="21" />
        <path d="M17 30l4 4 8-9" />
      </>
    ),
  },
  {
    icon: "cube",
    title: "Broader evaluations",
    body: "More challenging tasks, richer environments, and comparison across approaches. The task suite keeps growing — from 8 to 10 tasks so far — to study agent reliability in more realistic settings.",
    art: (
      <>
        <path d="M22 6l12 7v14l-12 7-12-7V13z" />
        <path d="M22 6v14M10 13l12 7 12-7" />
      </>
    ),
  },
];

function ExtLink({ href, label, children }: { href: string; label: string; children: React.ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      aria-label={label}
      className="font-bold underline hover:text-[var(--color-signal)]"
    >
      {children}
    </a>
  );
}

/** / — Observatory landing page. Content is descriptive; experiment
 *  numbers below are computed live from the exported manifest data. */
export function HomePage() {
  return (
    <LabShell>
      {/* Hero */}
      <section aria-label="Introduction" className="grid gap-6 lg:grid-cols-5">
        <div className="lg:col-span-3">
          <h2 className="font-display mt-2 text-4xl font-bold leading-[1.05] tracking-tight sm:text-6xl">
            Making agent reliability measurable.
          </h2>
          <p className="mt-4 max-w-2xl leading-relaxed text-[var(--color-ink-soft)]">
            An open research project studying how reliably AI agents complete
            multi-step tasks. We build reproducible evaluations, inspect
            failure modes, and publish results that others can examine and
            reproduce.
          </p>
        </div>
        <div className="lg:col-span-2">
          <HeroChase />
        </div>
      </section>

      {/* What we're working on */}
      <HomeSection title="What we're working on">
        <div className="space-y-4">
          {WORK_AREAS.map((a, i) => (
            <Reveal key={a.title} delay={Math.min(i * 0.05, 0.15)}>
              <div className="card flex items-start gap-4 p-5">
                <Icon label={a.icon}>{a.art}</Icon>
                <div className="min-w-0">
                  <h3 className="font-display text-xl font-bold tracking-tight">{a.title}</h3>
                  <p className="mt-1 max-w-3xl text-sm leading-relaxed text-[var(--color-ink-soft)]">{a.body}</p>
                </div>
              </div>
            </Reveal>
          ))}
        </div>
      </HomeSection>

      {/* About the project */}
      <HomeSection title="About the project">
        <div className="grid gap-4 md:grid-cols-2">
          <div className="card p-5">
            <h3 className="font-display text-base font-bold">Why this project exists</h3>
            <p className="mt-1 text-sm leading-relaxed text-[var(--color-ink-soft)]">
              A model producing a plausible final answer does not mean an agent
              reliably completed a multi-step task. It may have skipped required
              lookups, cited numbers it never observed, or guessed the verdict.
              This project inspects the execution process itself — the evidence
              gathered, the tools used, and the final outcome — instead of
              grading answers alone.
            </p>
          </div>
          <div className="card p-5">
            <h3 className="font-display text-base font-bold">Our custom agent harness</h3>
            <p className="mt-1 text-sm leading-relaxed text-[var(--color-ink-soft)]">
              For each task, the harness (<span className="font-mono text-[13px]">run_agent</span>) loops
              up to a per-task <span className="font-mono text-[13px]">max_steps</span> model-call budget.
              Each turn sends the task prompt plus history to the model, which replies
              with a tool call or a final answer. Tool results return as observations;
              malformed tool output ends the trial, unknown tools fail it, and model
              errors are recorded — never silently retried. Every step, the final
              answer, and run provenance land in a recorded trajectory.
            </p>
          </div>
          <div className="card p-5">
            <h3 className="font-display text-base font-bold">The MiniBank task environment</h3>
            <p className="mt-1 text-sm leading-relaxed text-[var(--color-ink-soft)]">
              Agents work in MiniBank, a small <em>simulated</em> bank fixture — not real
              accounts or customer data. It holds customers, accounts with balances,
              timestamped transactions, and one policy document. Five read-only tools
              expose it: <span className="font-mono text-[13px]">get_customer</span>,{" "}
              <span className="font-mono text-[13px]">get_account</span>,{" "}
              <span className="font-mono text-[13px]">get_transactions</span>,{" "}
              <span className="font-mono text-[13px]">search_policy</span>,{" "}
              <span className="font-mono text-[13px]">calculate</span>. Ten tasks exercise
              reliable behavior — e.g. retrieving checking balance 800, summing deposits
              to 600, applying waiver rules (average balance ≥ 1500 or 30-day deposits
              ≥ 500), or combining checking balances to 2600.
            </p>
          </div>
          <div className="card p-5">
            <h3 className="font-display text-base font-bold">How an experiment is evaluated</h3>
            <p className="mt-1 text-sm leading-relaxed text-[var(--color-ink-soft)]">
              A deterministic rule checker — no LLM judge — verifies verdict wording,
              required evidence, required tool calls, and that cited numbers appear in
              tool observations. Trials end as valid, provider/infrastructure errors,
              or evaluator errors; faults are recorded as trial status and excluded
              from reliability, never charged to the agent. Failures classify into a
              fixed taxonomy (tool misuse, evidence gap, policy misread, arithmetic
              error, premature stop, format violation). Reported reliability is
              passed ÷ valid trials.
            </p>
          </div>
          <div className="card p-5">
            <h3 className="font-display text-base font-bold">Reproducibility and published results</h3>
            <p className="mt-1 text-sm leading-relaxed text-[var(--color-ink-soft)]">
              Every trial records its model, temperature, seed labels, budgets, and
              full trajectory in append-only JSONL artifacts, preserved locally and
              intentionally kept out of the repository. What is published: study reports
              under <span className="font-mono text-[13px]">docs/experiments</span>, plus
              dashboard data generated from the artifacts through a manifest pipeline —
              which is exactly what renders the experiment pages on this site.
            </p>
          </div>
          <div className="card p-5">
            <h3 className="font-display text-base font-bold">Current scope and future work</h3>
            <p className="mt-1 text-sm leading-relaxed text-[var(--color-ink-soft)]">
              Today: a controlled banking fixture, one local model family, and
              pilot-scale trial counts. Under consideration, not committed: broader
              task domains and a possible public leaderboard. Nothing here claims
              general model superiority or production readiness.
            </p>
          </div>
        </div>
      </HomeSection>

      {/* Repository */}
      <HomeSection title="Explore the repository">
        <div className="card flex flex-col gap-4 p-5 sm:flex-row sm:items-center">
          <div className="min-w-0 flex-1">
            <p className="font-display break-words text-xl font-bold">crystalknife/agent-reliability-lab</p>
            <p className="mt-1 text-sm text-[var(--color-ink-soft)]">
              An experimental framework for measuring AI-agent reliability
              through controlled tasks, tool use, trajectories, and
              deterministic evaluation.
            </p>
          </div>
          <a
            href="https://github.com/crystalknife/agent-reliability-lab"
            target="_blank"
            rel="noreferrer"
            className="card-flat shrink-0 px-5 py-2.5 font-mono text-sm font-bold hover:bg-[var(--color-paper-deep)]"
            aria-label="Explore repo on GitHub"
          >
            Explore repo →
          </a>
        </div>
      </HomeSection>

      {/* Developer */}
      <HomeSection title="About the developer">
        <div className="card flex items-start gap-4 p-5">
          <svg width="44" height="44" viewBox="0 0 44 44" fill="none" role="img" aria-label="Developer">
            <circle cx="22" cy="14" r="7" stroke="var(--color-signal)" strokeWidth="2.5" />
            <path d="M8 38c2-8 7-12 14-12s12 4 14 12" stroke="var(--color-signal)" strokeWidth="2.5" strokeLinecap="round" />
          </svg>
          <div className="min-w-0">
            <p className="font-display text-xl font-bold tracking-tight">CRYSTALKNIPE</p>
            <p className="mt-1 max-w-3xl text-sm text-[var(--color-ink-soft)]">
              Maintainer of the Agent Reliability Lab.
            </p>
            <p className="mt-1 font-mono text-xs">
              <ExtLink href="https://github.com/crystalknife" label="Open crystalknife's GitHub profile">
                GitHub profile →
              </ExtLink>
            </p>
          </div>
        </div>
      </HomeSection>

      <ExperimentSpotlight />
      <Contribute />
    </LabShell>
  );
}

function ExperimentSpotlight() {
  const { error } = useManifest();
  return (
    <HomeSection title="Explore the experiments">
      <div className="card grid gap-4 p-5 sm:grid-cols-5">
        <div className="sm:col-span-3">
          <h3 className="font-display text-2xl font-bold tracking-tight sm:text-3xl">Explore the experiments.</h3>
          <ExperimentCounts />
          {error && <p className="mt-2 font-mono text-xs text-[var(--color-brick)]">Could not load experiment data.</p>}
          <div className="mt-4 flex flex-wrap gap-3">
            <Link
              to="/experiments"
              className="rounded-md bg-[var(--color-ink)] px-5 py-2.5 font-mono text-sm font-bold text-[var(--color-card)]"
            >
              View all experiments →
            </Link>
            <a
              href="https://github.com/crystalknife/agent-reliability-lab#experiments"
              target="_blank"
              rel="noreferrer"
              className="card-flat px-5 py-2.5 font-mono text-sm font-bold hover:bg-[var(--color-paper-deep)]"
            >
              Learn more
            </a>
          </div>
        </div>
        <div className="flex items-center justify-center sm:col-span-2">
          <svg width="120" height="120" viewBox="0 0 120 120" fill="none" role="img" aria-label="Flask">
            <path d="M48 10h24M52 10v34l-26 52a6 6 0 0 0 5 9h58a6 6 0 0 0 5-9L68 44V10" stroke="var(--color-signal)" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
            <line x1="40" y1="78" x2="80" y2="78" stroke="var(--color-signal)" strokeWidth="4" strokeLinecap="round" />
          </svg>
        </div>
      </div>
    </HomeSection>
  );
}

function ExperimentCounts() {
  const { entries } = useManifest();
  const [counts, setCounts] = React.useState<{
    trials: number; passed: number; n: number; loaded: number; failed: number;
  } | null>(null);
  React.useEffect(() => {
    if (!entries) return;
    let live = true;
    (async () => {
      let trials = 0;
      let passed = 0;
      let loaded = 0;
      let failed = 0;
      for (const e of entries) {
        try {
          // Rows only (not trajectories), through the same base-path-aware
          // loader and row guard as the dashboards.
          const res = await fetch(dataUrl(e.files.rows));
          if (!res.ok) {
            failed++;
            continue;
          }
          const rows = (await res.json() as unknown[]).filter(isTrialRow);
          const valid = validRows(rows);
          trials += valid.length;
          passed += valid.filter((r) => r.passed).length;
          loaded++;
        } catch {
          failed++;
        }
      }
      if (live) setCounts({ trials, passed, n: entries.length, loaded, failed });
    })();
    return () => { live = false; };
  }, [entries]);
  if (!counts) {
    return (
      <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[var(--color-ink-soft)]">
        Browse all experiments, compare results, and dive into detailed analyses.
      </p>
    );
  }
  if (counts.loaded === 0) {
    return (
      <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[var(--color-ink-soft)]">
        Experiment data is unavailable right now. Browse the experiments to inspect the published results.
      </p>
    );
  }
  const rate = counts.trials === 0 ? 0 : (100 * counts.passed) / counts.trials;
  return (
    <div className="mt-2 max-w-2xl text-sm leading-relaxed text-[var(--color-ink-soft)]">
      <p>
        Browse all experiments, compare results, and dive into detailed analyses.
        Each experiment tests a specific aspect of agent behavior with transparent methods and data.
      </p>
      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 font-mono text-xs sm:grid-cols-4">
        <div><dt className="text-[var(--color-ink-soft)]">Experiments</dt><dd className="tnum text-base font-bold text-[var(--color-ink)]">{counts.n}</dd></div>
        <div><dt className="text-[var(--color-ink-soft)]">Total trials</dt><dd className="tnum text-base font-bold text-[var(--color-ink)]">{counts.trials}</dd></div>
        <div><dt className="text-[var(--color-ink-soft)]">Successful trials</dt><dd className="tnum text-base font-bold text-[var(--color-ink)]">{counts.passed}</dd></div>
        <div><dt className="text-[var(--color-ink-soft)]">Overall success rate</dt><dd className="tnum text-base font-bold text-[var(--color-ink)]">{rate.toFixed(1)}%</dd></div>
      </dl>
      <p className="mt-1 font-mono text-[11px] text-[var(--color-ink-soft)]">
        {counts.failed > 0
          ? `Pooled across ${counts.loaded} of ${counts.n} published experiments; some data could not be loaded.`
          : `Pooled across all ${counts.n} published experiments; per-experiment rates differ.`}
      </p>
    </div>
  );
}

function Contribute() {
  return (
    <HomeSection title="Get involved">
      <div className="card p-5">
        <div>
          <h3 className="font-display text-2xl font-bold tracking-tight sm:text-3xl">Interested in contributing?</h3>
          <p className="mt-2 max-w-3xl text-sm leading-relaxed text-[var(--color-ink-soft)]">
            We welcome contributions, discussions, and collaboration. If you're
            interested in evaluation methods, benchmark design, or exploring
            future research directions, we'd love to hear from you. Changes
            land through pull requests with CI passing, per the project's
            contributing guide.
          </p>
          <div className="mt-4">
            <a
              href="https://github.com/crystalknife/agent-reliability-lab/issues"
              target="_blank"
              rel="noreferrer"
              className="inline-block rounded-md bg-[var(--color-ink)] px-5 py-2.5 font-mono text-sm font-bold text-[var(--color-card)]"
              aria-label="Contact us through GitHub issues"
            >
              Contact us →
            </a>
          </div>
        </div>
      </div>
    </HomeSection>
  );
}
