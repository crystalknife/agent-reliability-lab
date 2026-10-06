import { NavLink } from "react-router-dom";
import { cn } from "../lib/cn";

const LINKS = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/experiments", label: "Experiments", end: true },
];

export function LabShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto min-h-screen w-full max-w-6xl px-4 pb-24 sm:px-8">
      <header className="flex flex-col items-start gap-3 border-b-2 border-[var(--color-line)] py-6 sm:flex-row sm:flex-wrap sm:items-end sm:justify-between sm:gap-4">
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.25em] text-[var(--color-ink-faint)]">
            Mini Agent Reliability Lab
          </p>
          <h1 className="font-display text-3xl font-bold tracking-tight sm:text-4xl">
            Observatory<span className="text-[var(--color-signal)]">.</span>
          </h1>
        </div>
        <nav aria-label="Primary" className="flex gap-1">
          {LINKS.map((l) => (
            <NavLink
              key={l.to}
              to={l.to}
              end={l.end}
              className={({ isActive }) =>
                cn(
                  "rounded-md px-3 py-1.5 font-mono text-xs uppercase tracking-wider",
                  isActive
                    ? "bg-[var(--color-ink)] text-[var(--color-card)]"
                    : "text-[var(--color-ink-soft)] hover:text-[var(--color-ink)]",
                )
              }
            >
              {l.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main className="pt-8">{children}</main>
      <footer className="mt-16 border-t border-[var(--color-ink-faint)] pt-4 font-mono text-xs text-[var(--color-ink-faint)]">
        Read-only observability over experiment artifacts. Python engine remains authoritative.
      </footer>
    </div>
  );
}
