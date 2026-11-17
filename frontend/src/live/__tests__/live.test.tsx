/**
 * Live pages against a fake backend (fetch + EventSource doubles).
 * All data in this file is SYNTHETIC test data shaped like the live API.
 * @vitest-environment jsdom
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { duration, LiveOverviewPage, LiveRunPage, SourceProvenance } from "../LivePages";
import { RETRY_DELAYS_MS } from "../api";
import type { LiveAnalytics, LiveDetail, LiveExperiment, RateBlock } from "../types";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  closed = false;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners: Record<string, ((e: MessageEvent) => void)[]> = {};
  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, fn: (e: MessageEvent) => void) {
    (this.listeners[type] ??= []).push(fn);
  }
  close() {
    this.closed = true;
  }
  emit(id: number, type: string, payload: Record<string, unknown> = {}, experiment_id = "exp-1") {
    const data = JSON.stringify({ id, type, experiment_id, ts: 1, payload });
    for (const fn of this.listeners[type] ?? []) fn({ data } as MessageEvent);
  }
  static get last() {
    return FakeEventSource.instances[FakeEventSource.instances.length - 1];
  }
}

const rate = (passed: number, valid: number, extra: Partial<RateBlock> = {}): RateBlock => ({
  passed, valid, rate: valid ? passed / valid : null, interval: null,
  insufficient_sample: valid < 5, notes: [], ...extra,
});

function experiment(over: Partial<LiveExperiment> = {}, finished = 1, total = 3): LiveExperiment {
  return {
    experiment_id: "exp-1", status: "running", kind: "synthetic", source: "live_backend",
    config: {
      experiment_id: "exp-1", kind: "synthetic", label: "SYNTHETIC scripted / sequential", model: "scripted",
      provider: "local-stub", framework: "minilab", strategy: "normal", exec_mode: "sequential",
      temperature: 0, seed: 0, repeats: 1, task_ids: ["t-a", "t-b", "t-c"], total_trials: total,
      code_revision: "0123456789abcdef", code_dirty: null, task_schema_version: "1", protocol_ref: null,
    },
    error: null, created_at: 1000, started_at: 1001, finished_at: null, updated_at: 1002,
    current_task: "t-b",
    progress: { finished, started: finished + 1, total, remaining: total - finished, fraction: finished / total },
    elapsed_s: 12.5, stale: false, stale_reason: null,
    recording: { status: "in_progress", reasons: [], write_failures: 0, failures_by_hook: {}, failure_types: {},
      authoritative_evidence: "JSONL result and trajectory files" },
    ...over,
  };
}

function analytics(valid: number, passed: number): LiveAnalytics {
  return {
    counts: { trials_started: valid, trials_finished: valid, trials_running: 0, valid, excluded: 0,
      by_trial_status: { valid }, passed, failed_valid: valid - passed, tasks: 1 },
    pass_rate: rate(passed, valid, valid && valid < 5
      ? { notes: ["fewer than 5 valid trials: interval not shown (display convention)"] } : {}),
    failure_distribution: valid - passed ? { evidence_gap: valid - passed } : {},
    reason_categories: {}, termination_reasons: { agent_final: valid },
    by_task: [{ task_id: "t-a", schema_version: "1", family: null, declared_depth: null,
      depth_status: "undeclared: legacy schema-1 task", candidate_depths: { x: 2, y: 1 }, difficulty: "easy",
      trials: valid, excluded: 0, ...rate(passed, valid) }],
    by_difficulty: {}, by_depth: { available: false, reason: "no task in this run has a declared depth" },
    by_family: { available: false, reason: "no task family is defined for the tasks in this run" },
    tool_calls_total: 4, model_requests_total: 6,
    provenance_completeness: { verified: 20, indeterminate: 2, not_observable: 1 }, min_valid_for_interval: 5,
  };
}

function detail(exp: LiveExperiment, valid = 2, passed = 1): LiveDetail {
  const info = { task_id: "t-a", schema_version: "1", family: null, declared_depth: null,
    depth_status: "undeclared", candidate_depths: null, difficulty: "easy" };
  return {
    experiment: exp, analytics: analytics(valid, passed),
    trials: [{ run_id: "r1", task_id: "t-a", seq: 1, state: "finished", started_at: 1, finished_at: 2, task: info,
      trial_status: "valid", passed: false, failure: "evidence_gap", termination_reason: "agent_final",
      tool_calls: 2, model_requests: 3,
      tool_activity: [{ iteration: 1, tool: "get_account", outcome: "ok" }, { iteration: 2, tool: "calculate", outcome: "error" }] },
    { run_id: "r2", task_id: "t-b", seq: 2, state: "running", started_at: 3, finished_at: null, task: info }],
  };
}

let responses: Record<string, () => unknown>;
let calls: string[];
const json = (body: unknown, status = 200) => ({
  ok: status >= 200 && status < 300, status, headers: { get: () => "application/json" }, json: async () => body,
});
const HEALTH = { status: "ok", api_version: "1", server_time: 1, uptime_s: 5, code_revision: "abcdef0123456",
  last_event_id: 0, synthetic_runs_enabled: false, authentication: "none (loopback only)",
  data_layers_served: ["sanitized run metadata", "aggregated analytics"] };

beforeEach(() => {
  FakeEventSource.instances = [];
  calls = [];
  responses = { "/api/health": () => HEALTH, "/api/experiments": () => ({ experiments: [], server_time: 1 }) };
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.stubGlobal("fetch", async (url: string) => {
    calls.push(url);
    const hit = responses[url];
    if (!hit) return json({ error: "not found" }, 404);
    return json(hit());
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const overview = () => render(<MemoryRouter initialEntries={["/live"]}><LiveOverviewPage /></MemoryRouter>);
const runPage = (id = "exp-1") => render(
  <MemoryRouter initialEntries={[`/live/runs/${id}`]}>
    <Routes><Route path="/live/runs/:id" element={<LiveRunPage />} /></Routes>
  </MemoryRouter>);

describe("live overview", () => {
  it("says the backend is offline and shows no numbers when /api is unreachable", async () => {
    vi.stubGlobal("fetch", async () => { throw new TypeError("network"); });
    overview();
    await screen.findByTestId("backend-offline");
    expect(screen.getByTestId("connection").dataset.connection).toBe("offline");
    expect(screen.queryByTestId("run-row")).toBeNull();
    expect(screen.queryByTestId("idle")).toBeNull();          // "idle" would be a claim about a backend we cannot see
    expect(document.body.textContent).toContain("legacy exported experiments");
  });

  it("treats a static host answering /api with HTML as no backend", async () => {
    vi.stubGlobal("fetch", async () => ({ ok: true, status: 200, headers: { get: () => "text/html" }, json: async () => ({}) }));
    overview();
    await screen.findByTestId("backend-offline");
  });

  it("shows a truthful idle state with an empty history", async () => {
    overview();
    await screen.findByTestId("idle");
    expect(screen.getByTestId("no-history")).toBeTruthy();
    expect(screen.queryByTestId("run-row")).toBeNull();
    expect(document.body.textContent).toContain("none (loopback only)");
  });

  it("updates progress when the event stream reports a finished trial", async () => {
    let finished = 1;
    responses["/api/experiments"] = () => ({ experiments: [experiment({}, finished)], server_time: 1 });
    overview();
    await waitFor(() => expect(screen.getByTestId("progress-text").textContent).toContain("1 / 3"));
    act(() => FakeEventSource.last.onopen?.());
    expect(screen.getByTestId("connection").dataset.connection).toBe("live");
    finished = 2;
    act(() => FakeEventSource.last.emit(7, "trial_finished", { task_id: "t-b" }));
    await waitFor(() => expect(screen.getByTestId("progress-text").textContent).toContain("2 / 3"));
    expect(screen.getByTestId("event-list").textContent).toContain("trial finished");
    expect(screen.getByTestId("run-row").textContent).toContain("synthetic");
  });

  it("separates active runs from history and shows a failed run's error type", async () => {
    responses["/api/experiments"] = () => ({ server_time: 1, experiments: [
      experiment({ experiment_id: "a" }),
      experiment({ experiment_id: "b", status: "failed", error: { error_type: "KeyError" }, current_task: null }),
      experiment({ experiment_id: "c", status: "running", stale: true, stale_reason: "no update for 500 s" }),
    ] });
    overview();
    await waitFor(() => expect(screen.getAllByTestId("run-row").length).toBe(3));
    expect(document.querySelector('[aria-label="Active runs"]')!.querySelectorAll('[data-testid="run-row"]').length).toBe(2);
    const hist = document.querySelector('[aria-label="Run history"]')!;
    expect(hist.textContent).toContain("run error: KeyError");
    expect(document.body.textContent).toContain("no update for 500 s");
  });
});

describe("event stream reconnection", () => {
  it("reconnects with the last event id and never replays an event", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    responses["/api/experiments"] = () => ({ experiments: [experiment()], server_time: 1 });
    overview();
    await waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
    const first = FakeEventSource.last;
    expect(first.url).toBe("/api/stream?after=0");
    act(() => { first.onopen?.(); first.emit(5, "trial_started"); first.emit(6, "trial_finished"); });
    act(() => first.emit(6, "trial_finished"));               // duplicate: ignored
    act(() => first.emit(4, "trial_started"));                // stale: ignored
    expect(screen.getByTestId("event-list").querySelectorAll("li").length).toBe(2);

    act(() => first.onerror?.());
    expect(first.closed).toBe(true);
    expect(screen.getByTestId("connection").dataset.connection).toBe("reconnecting");
    await act(async () => { await vi.advanceTimersByTimeAsync(RETRY_DELAYS_MS[0] + 10); });
    expect(FakeEventSource.instances.length).toBe(2);
    expect(FakeEventSource.last.url).toBe("/api/stream?after=6");
    const before = calls.filter((c) => c === "/api/experiments").length;
    act(() => FakeEventSource.last.onopen?.());
    expect(screen.getByTestId("connection").dataset.connection).toBe("live");
    await waitFor(() => expect(calls.filter((c) => c === "/api/experiments").length).toBeGreaterThan(before));
  });

  it("reports offline after repeated failures and keeps trying", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    responses["/api/experiments"] = () => ({ experiments: [experiment()], server_time: 1 });
    overview();
    await waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
    for (let i = 0; i < 4; i += 1) {
      act(() => FakeEventSource.last.onerror?.());
      await act(async () => { await vi.advanceTimersByTimeAsync(RETRY_DELAYS_MS[RETRY_DELAYS_MS.length - 1] + 10); });
    }
    act(() => FakeEventSource.last.onerror?.());
    expect(screen.getByTestId("connection").dataset.connection).toBe("offline");
    expect(FakeEventSource.instances.length).toBeGreaterThanOrEqual(5);
  });
});

describe("live run page", () => {
  it("labels a synthetic run, flags the small sample and invents no depth or family", async () => {
    responses["/api/experiments/exp-1"] = () => detail(experiment());
    runPage();
    await screen.findByTestId("synthetic-banner");
    const pr = screen.getByTestId("rate-pass rate");
    expect(pr.textContent).toContain("50.0%");
    expect(pr.textContent).toContain("1 passed of 2 valid");
    expect(pr.textContent).toContain("insufficient sample");
    expect(pr.textContent).not.toContain("Wilson interval");
    expect(screen.getByTestId("unavailable-by depth").textContent).toContain("no task in this run has a declared depth");
    expect(screen.getByTestId("unavailable-by family")).toBeTruthy();
    expect(screen.getByTestId("task-table").textContent).toContain("undeclared");
    expect(screen.getByTestId("task-table").textContent).toContain("not defined");
    const rows = screen.getAllByTestId("trial-row");
    expect(rows.length).toBe(2);
    expect(rows[0].textContent).toContain("evidence gap");
    expect(rows[1].textContent).toContain("running");
  });

  it("shows an interval only when the backend provides one", async () => {
    const d = detail(experiment({ status: "completed", current_task: null }), 6, 3);
    d.analytics.pass_rate = rate(3, 6, { interval: { lower: 0.188, upper: 0.812, confidence: 0.95, method: "wilson" },
      insufficient_sample: false, notes: ["trials are clustered by task"] });
    responses["/api/experiments/exp-1"] = () => d;
    runPage();
    const pr = await screen.findByTestId("rate-pass rate");
    expect(pr.textContent).toContain("95% Wilson interval 18.8% to 81.2%");
    expect(pr.textContent).toContain("clustered by task");
    expect(pr.textContent).not.toContain("insufficient sample");
  });

  it("shows no rate at all when there are no valid trials", async () => {
    responses["/api/experiments/exp-1"] = () => detail(experiment(), 0, 0);
    runPage();
    const pr = await screen.findByTestId("rate-pass rate");
    expect(pr.textContent).toContain("no valid trials");
    expect(pr.textContent).not.toContain("%");
  });

  it("shows a failed run with its error type and keeps finished trials", async () => {
    responses["/api/experiments/exp-1"] = () => detail(experiment({
      status: "failed", error: { error_type: "RuntimeError" }, current_task: null, finished_at: 1010 }));
    runPage();
    expect((await screen.findByTestId("run-error")).textContent).toContain("RuntimeError");
    expect(screen.getAllByTestId("trial-row").length).toBe(2);
  });

  it("makes an incomplete recording unmistakable and does not call it a failure", async () => {
    const d = detail(experiment({ status: "completed", current_task: null, recording: {
      status: "incomplete", reasons: ["1 trial(s) have a start record but no finish record"], write_failures: 1,
      failures_by_hook: { trial_finished: 1 }, failure_types: { OperationalError: 1 },
      authoritative_evidence: "JSONL result and trajectory files" } }));
    d.trials[1].state = "unrecorded";
    responses["/api/experiments/exp-1"] = () => d;
    runPage();
    const notice = await screen.findByTestId("recording-notice");
    expect(notice.textContent).toContain("This dashboard record is incomplete");
    expect(notice.textContent).toContain("not an agent failure");
    expect(notice.textContent).toContain("JSONL result and trajectory files");
    expect(notice.textContent).toContain("trial finished: 1 failed write(s)");
    expect(screen.getByTestId("recording-badge").dataset.recording).toBe("incomplete");
    expect(screen.getAllByTestId("trial-row")[1].textContent).toContain("finish not recorded");
    expect(screen.queryByTestId("run-error")).toBeNull();
  });

  it("shows a complete recording without a notice, and an unavailable one with it", async () => {
    const rec = (status: "complete" | "unavailable") => ({ status, reasons: status === "complete" ? [] : ["the recorder did not close this record"],
      write_failures: 0, failures_by_hook: {}, failure_types: {}, authoritative_evidence: "JSONL result and trajectory files" });
    responses["/api/experiments/exp-1"] = () => detail(experiment({ status: "completed", recording: rec("complete") }));
    const first = runPage();
    await waitFor(() => expect(screen.getByTestId("recording-badge").dataset.recording).toBe("complete"));
    expect(screen.queryByTestId("recording-notice")).toBeNull();
    first.unmount();
    responses["/api/experiments/exp-1"] = () => detail(experiment({ status: "completed", recording: rec("unavailable") }));
    runPage();
    expect((await screen.findByTestId("recording-notice")).textContent).toContain("could not be established");
  });

  it("flags a pass rate and a comparison computed from a recording not known to be complete", async () => {
    const d = detail(experiment({ status: "completed", current_task: null }));
    d.analytics.recording_status = "incomplete";
    d.analytics.recording_caveat = "recording incomplete: trials may be missing from this figure";
    d.analytics.pass_rate.notes.push(d.analytics.recording_caveat);
    responses["/api/experiments/exp-1"] = () => d;
    responses["/api/experiments"] = () => ({ server_time: 1, experiments: [
      experiment({ status: "completed" }), experiment({ experiment_id: "exp-2", status: "completed" })] });
    responses["/api/compare?a=exp-1&b=exp-2"] = () => ({ a: "exp-1", b: "exp-2", comparison: {
      kind: "descriptive", a: rate(1, 2), b: rate(2, 2), shared_tasks: 1, difference: -0.5, interval: null,
      interval_suppressed: "shared_tasks", insufficient_sample: true,
      recording: { a: "incomplete", b: "complete" },
      recording_caveat: { a: "recording incomplete: trials may be missing from this figure" },
      notes: ["descriptive only", "run A: recording incomplete: trials may be missing from this figure"] } });
    runPage();
    expect(await screen.findByTestId("rate-recording-caveat")).toBeTruthy();
    expect(screen.getByTestId("rate-pass rate").textContent).toContain("trials may be missing");
    const select = await screen.findByTestId("compare-select");
    await waitFor(() => expect(select.querySelectorAll("option").length).toBe(2));
    fireEvent.change(select, { target: { value: "exp-2" } });
    expect((await screen.findByTestId("compare-recording-caveat")).textContent).toContain("recording not complete: A");
    expect(screen.getByTestId("comparison").textContent).toContain("run A: recording incomplete");
  });

  it("shows no recording caveat when the record is complete", async () => {
    const d = detail(experiment({ status: "completed", current_task: null }));
    d.analytics.recording_status = "complete";
    d.analytics.recording_caveat = null;
    responses["/api/experiments/exp-1"] = () => d;
    runPage();
    await screen.findByTestId("rate-pass rate");
    expect(screen.queryByTestId("rate-recording-caveat")).toBeNull();
  });

  it("distinguishes an unknown run from an unreachable backend", async () => {
    runPage("missing");
    await screen.findByTestId("unknown-run");
    expect(screen.queryByTestId("backend-offline")).toBeNull();
  });

  it("refreshes the run when its events arrive", async () => {
    let finished = 1;
    responses["/api/experiments/exp-1"] = () => detail(experiment({}, finished));
    runPage();
    await waitFor(() => expect(screen.getByTestId("progress-text").textContent).toContain("1 / 3"));
    finished = 3;
    act(() => { FakeEventSource.last.onopen?.(); FakeEventSource.last.emit(9, "experiment_completed"); });
    await waitFor(() => expect(screen.getByTestId("progress-text").textContent).toContain("3 / 3"));
  });
});

describe("source provenance", () => {
  const field = <T,>(value: T) => ({ status: "available" as const, value });
  const manifest = (state: "clean" | "dirty") => ({
    status: "available" as const, git_commit: field("0123456789abcdef0123456789abcdef01234567"),
    worktree: field({ state, changed_tracked_files: state === "dirty" ? 2 : 0, untracked_files: 0, scope: ["src"] }),
    source_ref: field(state === "clean" ? "minilab-src@01234567#aaaaaaaa" : "minilab-src@WT#bbbbbbbb"),
    source_is_exact_commit: field(state === "clean"),
    tasks: field({ schema_version: "1", count: 3, sha256: "x", ref: "tasks@schema1#cccccccc" }),
    config: field({ sha256: "y", ref: "config#dddddddd" }),
    protocol: { status: "unavailable" as const, value: null, reason: "no protocol revision was supplied; none is inferred" },
  });
  const withManifest = (m: unknown) => {
    const e = experiment();
    (e.config as unknown as { manifest: unknown }).manifest = m;
    return e;
  };

  it("warns loudly when the working tree was dirty and never cites the commit as the source", () => {
    render(<SourceProvenance exp={withManifest(manifest("dirty"))} />);
    expect(screen.getByTestId("dirty-tree").textContent).toContain("not a reproduction of that commit");
    expect(screen.getByTestId("manifest").textContent).toContain("minilab-src@WT#bbbbbbbb");
    expect(screen.getByTestId("manifest").textContent).toContain("none is inferred");
  });

  it("shows a clean tree without a warning", () => {
    render(<SourceProvenance exp={withManifest(manifest("clean"))} />);
    expect(screen.queryByTestId("dirty-tree")).toBeNull();
    expect(screen.getByTestId("manifest").dataset.worktree).toBe("clean");
  });

  it("reports a manifest file that was not written, separately from the manifest itself", () => {
    const e = withManifest(manifest("clean"));
    e.config.manifest_persistence = { status: "failed", error_type: "FileExistsError" };
    render(<SourceProvenance exp={e} />);
    expect(screen.getByTestId("manifest-file-failed").textContent).toContain("was not written");
    expect(screen.getByTestId("manifest-file-failed").textContent).toContain("FileExistsError");
    expect(screen.getByTestId("manifest").dataset.worktree).toBe("clean");     // the manifest itself is still shown
    cleanup();
    e.config.manifest_persistence = { status: "written" };
    render(<SourceProvenance exp={e} />);
    expect(screen.getByTestId("manifest-file-written")).toBeTruthy();
    expect(screen.queryByTestId("manifest-file-failed")).toBeNull();
  });

  it("says so when no manifest exists", () => {
    render(<SourceProvenance exp={experiment()} />);
    expect(screen.getByTestId("manifest-unavailable").textContent).toContain("does not establish which source actually ran");
  });
});

describe("formatting", () => {
  it("formats durations", () => {
    expect(duration(null)).toBe("n/a");
    expect(duration(12.34)).toBe("12.3 s");
    expect(duration(125)).toBe("2 min 5 s");
    expect(duration(3725)).toBe("1 h 2 min");
  });
});
