import * as React from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { SmoothRoot } from "./hooks/SmoothRoot";

const DashboardPage = React.lazy(() =>
  import("./pages/pages").then((m) => ({ default: m.DashboardPage })));
const ExperimentPage = React.lazy(() =>
  import("./pages/pages").then((m) => ({ default: m.ExperimentPage })));
const ExperimentsPage = React.lazy(() =>
  import("./pages/pages").then((m) => ({ default: m.ExperimentsPage })));
const TaskPage = React.lazy(() =>
  import("./pages/pages").then((m) => ({ default: m.TaskPage })));
const TrialsPage = React.lazy(() =>
  import("./pages/pages").then((m) => ({ default: m.TrialsPage })));
const TrialPage = React.lazy(() =>
  import("./pages/pages").then((m) => ({ default: m.TrialPage })));

function Loading() {
  return <p className="p-8 font-mono text-sm">Loading view…</p>;
}

export function App() {
  return (
    <SmoothRoot>
      <BrowserRouter>
        <React.Suspense fallback={<Loading />}>
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/experiments" element={<ExperimentsPage />} />
            <Route path="/experiments/:id" element={<ExperimentPage />} />
            <Route path="/experiments/:id/tasks/:taskId" element={<TaskPage />} />
            <Route path="/experiments/:id/trials" element={<TrialsPage />} />
            <Route path="/experiments/:id/trials/:runId" element={<TrialPage />} />
            <Route path="*" element={<DashboardPage />} />
          </Routes>
        </React.Suspense>
      </BrowserRouter>
    </SmoothRoot>
  );
}
