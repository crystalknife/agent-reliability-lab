import { describe, expect, it } from "vitest";
import { agentReliability, failureHistogram, rate, statusCounts, validRows } from "../../lib/selectors";
import { arcOffset } from "../../lib/format";
import { isTrajectory, isTrialRow } from "../../lib/guards";
import type { TrialRow } from "../../types/domain";

/**
 * Pinned against EXP-001 (minilab.metrics output, 2026-10-06):
 * total 16, valid 16, provider_errors 0, reliability 0.25,
 * failures {evidence_gap: 8, policy_misread: 4, none: 4}.
 * If these change, either the fixture or the formula drifted — investigate,
 * do not "fix" the numbers. Python remains authoritative.
 */
function row(partial: Partial<TrialRow> & { run_id: string }): TrialRow {
  return {
    human_run_id: "h", experiment_id: "e", timestamp: 0, seed: 0,
    model: "local:qwen3-4b", provider: "local", temperature: 0,
    max_steps: 8, task_id: "waiver-c101", difficulty: "easy",
    trial_status: "valid", passed: false, failure: "evidence_gap",
    termination_reason: "agent_final", reasons: [],
    ...partial,
  } as TrialRow;
}

function exp001(): TrialRow[] {
  const rows: TrialRow[] = [];
  const push = (n: number, p: Partial<TrialRow>) => {
    for (let i = 0; i < n; i++) rows.push(row({ run_id: `r${rows.length}`, ...p }));
  };
  push(2, { task_id: "calc-total-c101", passed: true, failure: "none" });
  push(2, { task_id: "retrieval-bal-a103", passed: true, failure: "none" });
  push(2, { task_id: "waiver-c101", failure: "evidence_gap" });
  push(2, { task_id: "waiver-c102", failure: "policy_misread" });
  push(2, { task_id: "waiver-c103", failure: "evidence_gap" });
  push(2, { task_id: "policy-attr-c102", failure: "policy_misread" });
  push(2, { task_id: "temporal-deposit-a103", failure: "evidence_gap" });
  push(2, { task_id: "horizon-checking-combined", difficulty: "hard", failure: "evidence_gap" });
  return rows;
}

describe("selectors mirror minilab.metrics", () => {
  const rows = exp001();
  it("counts total/valid/provider errors", () => {
    expect(rows.length).toBe(16);
    expect(validRows(rows).length).toBe(16);
    expect(statusCounts(rows)).toEqual({ valid: 16 });
  });
  it("computes agent reliability over valid trials only", () => {
    const r = agentReliability(rows);
    expect(r.n).toBe(16);
    expect(r.success_rate).toBeCloseTo(0.25, 10);
  });
  it("histograms failures over valid trials only", () => {
    expect(failureHistogram(rows)).toEqual({ evidence_gap: 8, policy_misread: 4, none: 4 });
  });
  it("excludes provider errors from reliability and failures", () => {
    const withProv = [...rows, row({ run_id: "px", trial_status: "provider_error", failure: "none" })];
    expect(agentReliability(withProv).n).toBe(16);
    expect(failureHistogram(withProv)).toEqual({ evidence_gap: 8, policy_misread: 4, none: 4 });
    expect(statusCounts(withProv).provider_error).toBe(1);
  });
  it("rate handles empty input", () => {
    expect(rate([])).toEqual({ n: 0, success_rate: 0 });
  });
});

describe("reliability dial math", () => {
  const C = 2 * Math.PI * 54;
  it("maps 0/25%/100% to exact circumference remainders", () => {
    expect(arcOffset(0, C)).toBeCloseTo(C, 10);
    expect(arcOffset(0.25, C)).toBeCloseTo(0.75 * C, 10);
    expect(arcOffset(1, C)).toBeCloseTo(0, 10);
  });
  it("clamps out-of-range values instead of drawing garbage", () => {
    expect(arcOffset(-0.5, C)).toBeCloseTo(C, 10);
    expect(arcOffset(2, C)).toBeCloseTo(0, 10);
  });
  it("is data-driven, not hardcoded to EXP-001's 25%", () => {
    expect(arcOffset(0.5, C)).toBeCloseTo(0.5 * C, 10);
    expect(arcOffset(0.3333, C)).toBeCloseTo(0.6667 * C, 4);
  });
});

describe("guards", () => {
  it("accepts a real EXP-001 row shape", () => {
    expect(isTrialRow(row({ run_id: "x" }))).toBe(true);
  });
  it("rejects malformed rows", () => {
    expect(isTrialRow(null)).toBe(false);
    expect(isTrialRow({ run_id: "x" })).toBe(false);
    expect(isTrialRow({ ...row({ run_id: "x" }), trial_status: "bogus" })).toBe(false);
  });
  it("accepts a trajectory shape", () => {
    expect(isTrajectory({
      run_id: "r", task_id: "t", termination_reason: "agent_final",
      steps: [{ step: 1, action: "get_account", arguments: {}, observation: [] }],
    })).toBe(true);
  });
});
