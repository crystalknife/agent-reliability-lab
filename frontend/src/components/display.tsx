import { Link } from "react-router-dom";
import { motion } from "motion/react";
import type { ReactNode } from "react";
import { Pill, Tip } from "./anim/primitives";
import { formatPct } from "../lib/selectors";
import { arcOffset } from "../lib/format";
import type { Failure, TrialStatus } from "../types/domain";

export function Reveal({ children, delay = 0 }: { children: ReactNode; delay?: number }) {
  // Animate on mount, never on viewport entry: if an IntersectionObserver
  // misfires (throttled tab, odd viewport, headless), content still ends in
  // its final visible state. Below-fold items simply finish before seen.
  return (
    <motion.div
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, delay, ease: [0.32, 0.72, 0, 1] }}
    >
      {children}
    </motion.div>
  );
}

export function SectionTitle({ index, title, hint }: { index: string; title: string; hint?: string }) {
  return (
    <div className="mb-4 flex items-baseline gap-3">
      <span className="font-mono text-xs text-[var(--color-signal)]">{index}</span>
      <h2 className="font-display text-xl font-bold tracking-tight sm:text-2xl">{title}</h2>
      {hint && <span className="font-mono text-xs text-[var(--color-ink-faint)]">{hint}</span>}
    </div>
  );
}

export function StatusPill({ status }: { status: TrialStatus }) {
  const tone = status === "valid" ? "neutral" : "warn";
  return <Pill tone={tone}>{status.replace("_", " ")}</Pill>;
}

export function PassPill({ passed }: { passed: boolean }) {
  return <Pill tone={passed ? "pass" : "fail"}>{passed ? "pass" : "fail"}</Pill>;
}

export function FailurePill({ failure }: { failure: Failure }) {
  const tone = failure === "none" ? "neutral" : failure === "tool_misuse" ? "warn" : "fail";
  return <Pill tone={tone}>{failure.replace("_", " ")}</Pill>;
}

/** Big reliability numeral with tabular figures. */
export function ReliabilityDial({ value, n, label }: { value: number; n: number; label: string }) {
  const r = 54;
  const c = 2 * Math.PI * r;
  const final = arcOffset(value, c);
  return (
    <div className="flex items-center gap-5">
      <svg width="132" height="132" viewBox="0 0 132 132" role="img" aria-label={`${label} ${formatPct(value)} over ${n} trials`}>
        <circle cx="66" cy="66" r={r} fill="none" strokeWidth="10" className="stroke-[var(--color-paper-deep)]" />
        <motion.circle
          cx="66" cy="66" r={r} fill="none"
          className="stroke-[var(--color-signal)]"
          strokeWidth="10" strokeLinecap="butt"
          strokeDasharray={c} transform="rotate(-90 66 66)"
          // Progressive enhancement: the attribute holds the FINAL value, so
          // the arc is correct even if the animation never runs; motion only
          // animates toward it. Reduced-motion jumps straight to final.
          strokeDashoffset={final}
          initial={{ strokeDashoffset: c }}
          animate={{ strokeDashoffset: final }}
          transition={{ duration: 0.9, ease: [0.32, 0.72, 0, 1] }}
        />
        <text x="66" y="72" textAnchor="middle" className="font-display tnum" fontSize="30" fontWeight="700" fill="var(--color-ink)">
          {formatPct(value)}
        </text>
      </svg>
      <div>
        <p className="font-mono text-xs uppercase tracking-wider text-[var(--color-ink-faint)]">{label}</p>
        <p className="tnum font-display text-2xl font-bold">{n} <span className="text-base font-normal">trials</span></p>
      </div>
    </div>
  );
}

/** Horizontal bar row for histograms. */
export function BarRow({ label, count, max, to }: { label: string; count: number; max: number; to?: string }) {
  const inner = (
    <div className="flex items-center gap-3">
      <span className="w-36 shrink-0 truncate font-mono text-xs" title={label.replace(/_/g, " ")}>{label.replace(/_/g, " ")}</span>
      <div className="h-5 flex-1 border border-[var(--color-line)] bg-[var(--color-paper)]">
        <motion.div
          className="h-full bg-[var(--color-ink)]"
          style={{ width: `${max === 0 ? 0 : (count / max) * 100}%` }}
          initial={{ opacity: 0.4 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.4 }}
        />
      </div>
      <span className="tnum w-8 text-right font-mono text-xs font-bold">{count}</span>
    </div>
  );
  return to ? <Link to={to} className="block rounded px-1 py-1 hover:bg-[var(--color-paper-deep)]">{inner}</Link> : inner;
}

export function Meta({ label, value, tip }: { label: string; value: string; tip?: string }) {
  return (
    <div>
      <dt className="font-mono text-[11px] uppercase tracking-wider text-[var(--color-ink-faint)]">{label}</dt>
      <dd className="tnum break-words font-mono text-sm">
        {tip ? <Tip text={tip}>{value}</Tip> : value}
      </dd>
    </div>
  );
}

export function Empty({ what }: { what: string }) {
  return <p className="card-flat p-6 font-mono text-sm text-[var(--color-ink-faint)]">No {what} in this experiment.</p>;
}
