/**
 * Route smoke tests: every route renders against the REAL exported EXP-001
 * data, and the dashboard shows the Python-verified numbers
 * (16 valid, 25% reliability). jsdom + fetch stub (no browser needed).
 * @vitest-environment jsdom
 */
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { dataUrl } from "../../lib/load";
import {
  DashboardPage, ExperimentPage, ExperimentsPage, TaskPage, TrialsPage, TrialPage,
} from "../../pages/pages";

const DATA = join(process.cwd(), "public", "data");

beforeAll(() => {
  const files: Record<string, string> = {};
  for (const f of ["manifest.json", "exp-001.json", "exp-001.traj.json", "tasks.json"]) {
    files[dataUrl(f)] = readFileSync(join(DATA, f), "utf-8");
  }
  vi.stubGlobal("fetch", async (url: string) => ({
    ok: url in files,
    json: async () => JSON.parse(files[url] ?? "null"),
  }));
  Object.defineProperty(window, "matchMedia", {
    value: () => ({ matches: true, addEventListener: () => {}, removeEventListener: () => {} }),
  });
});

afterEach(() => {
  document.body.innerHTML = "";
});

function at(path: string, el: React.ReactNode) {
  return render(<MemoryRouter initialEntries={[path]}>{el}</MemoryRouter>);
}

const routes = (
  <>
    <Route path="/" element={<DashboardPage />} />
    <Route path="/experiments" element={<ExperimentsPage />} />
    <Route path="/experiments/:id" element={<ExperimentPage />} />
    <Route path="/experiments/:id/tasks/:taskId" element={<TaskPage />} />
    <Route path="/experiments/:id/trials" element={<TrialsPage />} />
    <Route path="/experiments/:id/trials/:runId" element={<TrialPage />} />
  </>
);

describe("routes render real EXP-001 data", () => {
  it("dashboard shows 25% reliability over 16 valid trials", async () => {
    at("/", <Routes>{routes}</Routes>);
    await waitFor(() => expect(document.body.textContent).toContain("Local Qwen3-4B Baseline"));
    expect(document.body.textContent).toContain("25%");
    expect(document.body.textContent).toContain("valid trials");
    expect(document.body.textContent).toContain("evidence gap");
  });

  it("experiments list shows the artifact", async () => {
    at("/experiments", <Routes>{routes}</Routes>);
    await waitFor(() => expect(document.body.textContent).toContain("exp-001"));
    expect(document.body.textContent).toContain("16 trials");
  });

  it("task page lists its trials", async () => {
    at("/experiments/exp-001/tasks/waiver-c101", <Routes>{routes}</Routes>);
    await waitFor(() => expect(document.body.textContent).toContain("waiver-c101"));
    expect(document.body.textContent).toContain("2 trials");
  });

  it("trials page filters exist", async () => {
    at("/experiments/exp-001/trials", <Routes>{routes}</Routes>);
    await waitFor(() => expect(screen.getByRole("tablist", { name: "Filter trials" })).toBeTruthy());
  });

  it("trials page honors a deep-linked failure filter", async () => {
    at("/experiments/exp-001/trials?failure=evidence_gap", <Routes>{routes}</Routes>);
    await waitFor(() => expect(document.body.textContent).toContain("filtered:"));
    expect(document.body.textContent).toContain("failure: evidence gap");
    expect(document.body.textContent).toContain("waiver-c101");
    expect(document.body.textContent).not.toContain("waiver-c102");
  });

  it("trial page renders steps, final answer, and verdict", async () => {
    const manifest = JSON.parse(readFileSync(join(DATA, "exp-001.json"), "utf-8"));
    const runId = manifest[0].run_id as string;
    at(`/experiments/exp-001/trials/${runId}`, <Routes>{routes}</Routes>);
    await waitFor(() => expect(screen.getByText("final answer", { exact: false })).toBeTruthy());
    expect(document.body.textContent).toContain("evaluator verdict");
    expect(document.body.textContent).toContain("provenance");
  });
});
