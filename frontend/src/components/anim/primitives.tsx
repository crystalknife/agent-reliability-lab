/**
 * Hand-rolled motion primitives following the Animate UI pattern
 * (Radix-style accessible primitives + motion/react transitions).
 * Animate UI's own tabs/tooltip were verified as plain shadcn/Radix without
 * motion, so vendoring them added nothing — these carry the animation here.
 */
import * as React from "react";
import { motion } from "motion/react";
import { cn } from "../../lib/cn";

/* Tabs with animated active-indicator (layoutId underline). */
export function Tabs({
  tabs,
  value,
  onChange,
  label,
}: {
  tabs: { id: string; label: string; count?: number }[];
  value: string;
  onChange: (id: string) => void;
  label: string;
}) {
  return (
    <div role="tablist" aria-label={label} className="flex flex-wrap gap-1">
      {tabs.map((t) => {
        const active = t.id === value;
        return (
          <button
            key={t.id}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(t.id)}
            className={cn(
              "relative rounded-md px-3 py-2 font-mono text-xs uppercase tracking-wider",
              active ? "text-[var(--color-card)]" : "text-[var(--color-ink-soft)] hover:text-[var(--color-ink)]",
            )}
          >
            {active && (
              <motion.span
                layoutId="minilab-tab-ink"
                className="absolute inset-0 rounded-md bg-[var(--color-ink)]"
                transition={{ duration: 0.22, ease: [0.32, 0.72, 0, 1] }}
              />
            )}
            <span className="relative">
              {t.label}
              {t.count !== undefined && <span className="tnum"> · {t.count}</span>}
            </span>
          </button>
        );
      })}
    </div>
  );
}

/* Minimal accessible tooltip (hover + focus). */
export function Tip({ text, children }: { text: string; children: React.ReactNode }) {
  const [open, setOpen] = React.useState(false);
  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      {children}
      {open && (
        <motion.span
          role="tooltip"
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.15 }}
          className="card-flat absolute bottom-full left-1/2 z-20 mb-2 w-max max-w-64 -translate-x-1/2 px-2.5 py-1.5 text-xs"
        >
          {text}
        </motion.span>
      )}
    </span>
  );
}

/* Status pill: text + shape carry meaning, never color alone. */
const PILL: Record<string, string> = {
  pass: "bg-[var(--color-moss)] text-[var(--color-card)]",
  fail: "bg-[var(--color-brick)] text-[var(--color-card)]",
  warn: "bg-[var(--color-amber-deep)] text-[var(--color-card)]",
  neutral: "bg-[var(--color-paper-deep)] text-[var(--color-ink)]",
};

export function Pill({ tone, children }: { tone: keyof typeof PILL; children: React.ReactNode }) {
  return (
    <span className={cn("inline-flex items-center rounded-full px-2.5 py-0.5 font-mono text-[11px] font-bold uppercase tracking-wider", PILL[tone])}>
      {children}
    </span>
  );
}
