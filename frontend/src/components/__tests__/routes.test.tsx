/**
 * Route smoke tests: every route renders against the REAL exported EXP-001
 * data, and the dashboard shows the Python-verified numbers
 * (16 valid, 25% reliability). jsdom + fetch stub (no browser needed).
 * @vitest-environment jsdom
 */
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { BrowserRouter, MemoryRouter, Route, Routes } from "react-router-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { dataUrl } from "../../lib/load";
import {
  DashboardPage, ExperimentPage, ExperimentsPage, TaskPage, TrialsPage, TrialPage,
} from "../../pages/pages";
import { HomePage } from "../../pages/Home";

const DATA = join(process.cwd(), "public", "data");

beforeAll(() => {
  const files: Record<string, string> = {};
  for (const f of ["manifest.json", "exp-001.json", "exp-001.traj.json", "tasks.json",
    "exp-002.json", "exp-002.traj.json", "exp-002a.json", "exp-002a.traj.json",
    "exp-002b.json", "exp-002b.traj.json", "exp-002c.json", "exp-002c.traj.json"]) {
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
  window.history.replaceState(null, "", "/");
});

function at(path: string, el: React.ReactNode) {
  return render(<MemoryRouter initialEntries={[path]}>{el}</MemoryRouter>);
}

const routes = (
  <>
    <Route path="/" element={<HomePage />} />
    <Route path="/dashboard" element={<DashboardPage />} />
    <Route path="/experiments" element={<ExperimentsPage />} />
    <Route path="/experiments/:id" element={<ExperimentPage />} />
    <Route path="/experiments/:id/tasks/:taskId" element={<TaskPage />} />
    <Route path="/experiments/:id/trials" element={<TrialsPage />} />
    <Route path="/experiments/:id/trials/:runId" element={<TrialPage />} />
  </>
);

describe("homepage", () => {
  it("renders the Observatory landing with links to experiments and repo", async () => {
    at("/", <Routes>{routes}</Routes>);
    await waitFor(() => expect(document.body.textContent).toContain("Making agent reliability measurable"));
    expect(document.body.textContent).toContain("View all experiments");
    expect(document.body.textContent).toContain("Explore the experiments");
  });

  it("replaces the old hero card with the pursuit scene", async () => {
    at("/", <Routes>{routes}</Routes>);
    await waitFor(() => expect(screen.getByRole("img", { name: /tracking robot/i })).toBeTruthy());
    expect(document.body.textContent).not.toContain("Reliability is more than a pass rate");
  });
});

describe("routes render real EXP-001 data", () => {
  it("dashboard shows 25% reliability over 16 valid trials", async () => {
    at("/dashboard", <Routes>{routes}</Routes>);
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

describe("navigation controls", () => {
  const runId = () => {
    const manifest = JSON.parse(readFileSync(join(DATA, "exp-001.json"), "utf-8"));
    return manifest[0].run_id as string;
  };

  it("All Experiments button navigates to the experiments index", async () => {
    at("/experiments/exp-001", <Routes>{routes}</Routes>);
    await waitFor(() => expect(document.body.textContent).toContain("Local Qwen3-4B Baseline"));
    fireEvent.click(screen.getByRole("link", { name: "All Experiments" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Experiments" })).toBeTruthy());
    expect(document.body.textContent).toContain("16 trials");
  });

  it("All Experiments button is present on the pilot detail page", async () => {
    at("/experiments/exp-002", <Routes>{routes}</Routes>);
    await waitFor(() => expect(document.body.textContent).toContain("Five-arm comparison"));
    expect(screen.getByRole("link", { name: "All Experiments" })).toBeTruthy();
  });

  it("Back returns to the previous in-app page when history exists", async () => {
    window.history.replaceState({ idx: 0 }, "", "/");
    window.history.pushState({ idx: 1 }, "", "/experiments/exp-001");
    render(<BrowserRouter><Routes>{routes}</Routes></BrowserRouter>);
    await waitFor(() => expect(document.body.textContent).toContain("Local Qwen3-4B Baseline"));
    fireEvent.click(screen.getByRole("button", { name: /back/i }));
    await waitFor(() =>
      expect(document.body.textContent).toContain("Making agent reliability measurable"));
  });

  it("Back from a directly opened experiment falls back to the experiments index", async () => {
    at("/experiments/exp-001", <Routes>{routes}</Routes>);
    await waitFor(() => expect(document.body.textContent).toContain("Local Qwen3-4B Baseline"));
    fireEvent.click(screen.getByRole("button", { name: /back/i }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Experiments" })).toBeTruthy());
  });

  it("Back from a directly opened dashboard falls back to the homepage", async () => {
    at("/dashboard", <Routes>{routes}</Routes>);
    await waitFor(() => expect(document.body.textContent).toContain("Local Qwen3-4B Baseline"));
    fireEvent.click(screen.getByRole("button", { name: /back/i }));
    await waitFor(() =>
      expect(document.body.textContent).toContain("Making agent reliability measurable"));
  });

  it("Back from the experiments index reaches the homepage, which has no Back button", async () => {
    at("/experiments", <Routes>{routes}</Routes>);
    await waitFor(() => expect(screen.getByRole("heading", { name: "Experiments" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /back/i }));
    await waitFor(() =>
      expect(document.body.textContent).toContain("Making agent reliability measurable"));
    expect(screen.queryByRole("button", { name: /back/i })).toBeNull();
  });

  it("Back from a directly opened task falls back to its experiment", async () => {
    at("/experiments/exp-001/tasks/waiver-c101", <Routes>{routes}</Routes>);
    await waitFor(() => expect(document.body.textContent).toContain("waiver-c101"));
    fireEvent.click(screen.getByRole("button", { name: /back/i }));
    await waitFor(() => expect(document.body.textContent).toContain("Local Qwen3-4B Baseline"));
  });

  it("Back from the trial ledger falls back to its experiment", async () => {
    at("/experiments/exp-001/trials", <Routes>{routes}</Routes>);
    await waitFor(() => expect(screen.getByRole("tablist", { name: "Filter trials" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /back/i }));
    await waitFor(() => expect(document.body.textContent).toContain("Local Qwen3-4B Baseline"));
  });

  it("Back from a directly opened trial falls back to the trial ledger", async () => {
    at(`/experiments/exp-001/trials/${runId()}`, <Routes>{routes}</Routes>);
    await waitFor(() => expect(document.body.textContent).toContain("evaluator verdict"));
    fireEvent.click(screen.getByRole("button", { name: /back/i }));
    await waitFor(() => expect(screen.getByRole("tablist", { name: "Filter trials" })).toBeTruthy());
  });

  it.each([
    "/dashboard",
    "/experiments",
    "/experiments/exp-001",
    "/experiments/exp-001/trials",
    "/experiments/exp-001/tasks/waiver-c101",
  ])("Home link is present on %s", (path) => {
    at(path, <Routes>{routes}</Routes>);
    expect(screen.getByRole("link", { name: "Home" }).getAttribute("href")).toBe("/");
  });
});

describe("exp-002 pilot report", () => {
  it("experiments list distinguishes baseline from pilot arms", async () => {
    at("/experiments", <Routes>{routes}</Routes>);
    await waitFor(() => expect(document.body.textContent).toContain("exp-002"));
    expect(document.body.textContent).toContain("baseline");
    expect(document.body.textContent).toContain("pilot");
  });

  it("exp-002 report renders the data-driven five-arm comparison", async () => {
    at("/experiments/exp-002", <Routes>{routes}</Routes>);
    await waitFor(() => expect(document.body.textContent).toContain("Five-arm comparison"));
    const text = document.body.textContent ?? "";
    // Arm titles come from bundles/summary data, not hardcoded JSX.
    for (const name of ["Custom baseline", "LangChain unadjusted", "Chatter stripped",
      "Schema parity", "Schema + chatter"]) {
      expect(text).toContain(name);
    }
    // Derived interpretation labels prove the comparison computed.
    expect(text).toContain("Controlled — matches baseline");
    expect(text).toContain("Matches unadjusted arm");
    // Recovered raw data: normal and combined arms expose per-task sets.
    expect(text).toContain("calc-total-c101");
    expect(text).not.toContain("per-task breakdown unavailable");
  });

  it("control arms keep full trial inspection", async () => {
    at("/experiments/exp-002a/trials", <Routes>{routes}</Routes>);
    await waitFor(() => expect(screen.getByRole("tablist", { name: "Filter trials" })).toBeTruthy());
    expect(document.body.textContent).toContain("waiver-c101");
    expect(document.body.textContent).toContain("waiver-c103");
  });

  it("recovered arms expose trial and trajectory routes", async () => {
    at("/experiments/exp-002c/trials", <Routes>{routes}</Routes>);
    await waitFor(() => expect(screen.getByRole("tablist", { name: "Filter trials" })).toBeTruthy());
    expect(document.body.textContent).toContain("horizon-checking-combined");
  });
});
