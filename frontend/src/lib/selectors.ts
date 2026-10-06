/**
 * Presentation selectors. These formulas intentionally MIRROR
 * minilab/metrics.py (summarize/_rate) for display purposes.
 *
 * The Python implementation remains the SOLE authoritative definition of
 * benchmark semantics (valid trials, provider errors, agent reliability).
 * Nothing here redefines pass/fail, the failure taxonomy, or reliability;
 * it only re-presents already-recorded row fields for the UI.
 */
import type { Failure, TrialRow } from "../types/domain";

export interface Rate {
  n: number;
  success_rate: number;
}

export function rate(rows: TrialRow[]): Rate {
  if (rows.length === 0) return { n: 0, success_rate: 0 };
  const ok = rows.filter((r) => r.passed).length;
  return { n: rows.length, success_rate: ok / rows.length };
}

export function validRows(rows: TrialRow[]): TrialRow[] {
  return rows.filter((r) => r.trial_status === "valid");
}

export function providerErrors(rows: TrialRow[]): TrialRow[] {
  return rows.filter((r) => r.trial_status === "provider_error");
}

/** Agent reliability = passed / valid, exactly as metrics.py computes it. */
export function agentReliability(rows: TrialRow[]): Rate {
  return rate(validRows(rows));
}

/** Failure histogram over valid trials only (metrics.py `failures`). */
export function failureHistogram(rows: TrialRow[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const r of validRows(rows)) {
    out[r.failure] = (out[r.failure] ?? 0) + 1;
  }
  return out;
}

export function statusCounts(rows: TrialRow[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const r of rows) {
    out[r.trial_status] = (out[r.trial_status] ?? 0) + 1;
  }
  return out;
}

export function groupBy<T>(rows: TrialRow[], key: (r: TrialRow) => string): Record<string, T[] | TrialRow[]> {
  const out: Record<string, TrialRow[]> = {};
  for (const r of rows) {
    const k = key(r);
    (out[k] ??= []).push(r);
  }
  return out as Record<string, T[] | TrialRow[]>;
}

export const FAILURE_ORDER: Failure[] = [
  "none", "tool_misuse", "evidence_gap", "policy_misread",
  "arithmetic_error", "premature_stop", "format_violation",
];

export function formatPct(x: number): string {
  return `${Math.round(x * 100)}%`;
}
