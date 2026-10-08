import { NavLink } from "react-router-dom";
import { cn } from "../lib/cn";

const LINKS = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/experiments", label: "Experiments", end: true },
];

export function LabShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto min-h-screen w-full max-w-6xl px-4 pb-24 sm:px-8">
      <header className="flex flex-wrap items-end justify-between gap-3 border-b-2 border-[var(--color-line)] py-6 sm:gap-4">
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.25em] text-[var(--color-ink-faint)]">
            Agent Reliability Lab (MiniLAB)
          </p>
          <h1 className="font-display text-3xl font-bold tracking-tight sm:text-4xl">
            Observatory<span className="text-[var(--color-signal)]">.</span>
          </h1>
        </div>
        <nav aria-label="Primary" className="flex flex-wrap items-center gap-1">
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
          <a
            href="https://github.com/crystalknife"
            target="_blank"
            rel="noreferrer"
            aria-label="Open GitHub profile for crystalknife"
            className="inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-[var(--color-ink-soft)] hover:text-[var(--color-ink)]"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.58.11.79-.25.79-.56 0-.27-.01-1.17-.02-2.12-3.2.7-3.88-1.36-3.88-1.36-.52-1.33-1.28-1.68-1.28-1.68-1.04-.71.08-.7.08-.7 1.15.08 1.76 1.19 1.76 1.19 1.03 1.76 2.69 1.25 3.35.96.1-.75.4-1.25.72-1.54-2.55-.29-5.23-1.28-5.23-5.68 0-1.26.45-2.28 1.19-3.09-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.18 1.18a11.1 11.1 0 0 1 5.8 0c2.2-1.49 3.17-1.18 3.17-1.18.63 1.59.23 2.76.11 3.05.74.81 1.19 1.83 1.19 3.09 0 4.41-2.69 5.38-5.25 5.67.41.35.77 1.05.77 2.12 0 1.53-.01 2.76-.01 3.14 0 .31.21.68.8.56A10.52 10.52 0 0 0 23.5 12C23.5 5.65 18.35.5 12 .5Z" />
            </svg>
            GitHub&nbsp;&nbsp;crystalknife
          </a>
        </nav>
      </header>
      <main className="pt-8">{children}</main>
      <footer className="mt-16 border-t border-[var(--color-ink-faint)] pt-4 font-mono text-xs text-[var(--color-ink-faint)]">
        Read-only observability over experiment artifacts. Python engine remains authoritative.
      </footer>
    </div>
  );
}
