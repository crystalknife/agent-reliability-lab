/**
 * Types for the LIVE backend API (python -m minilab.live.server).
 *
 * Everything here is sanitized run metadata or aggregated analytics. There is
 * deliberately no field for prompts, tool arguments, observations or final
 * answers: the live API does not serve them. Do not add such fields here to
 * "make the UI work"; add a sanitized field on the server instead.
 */

export type RunStatus = "queued" | "running" | "completed" | "failed";
export type ObservedStatus = "verified" | "indeterminate" | "not_observable";

export interface LiveConfig {
  experiment_id: string;
  kind: "run" | "synthetic";
  label: string | null;
  model: string | null;
  provider: string | null;
  framework: string;
  strategy: string | null;
  exec_mode: string;
  temperature: number | null;
  seed: number | null;
  repeats: number | null;
  task_ids: string[];
  total_trials: number | null;
  code_revision: string | null;
  code_dirty: boolean | null;
  task_schema_version: string | null;
  protocol_ref: string | null;
  manifest?: RunManifest;
  /** Whether the optional manifest FILE was written; separate from whether a manifest could be built. */
  manifest_persistence?: { status: "written" | "failed" | "not_requested" | "not_recorded"; error_type?: string };
}

export interface ManifestField<T> {
  status: "available" | "unavailable";
  value: T | null;
  reason?: string;
}

/** Run-start provenance snapshot: hashes, counts, versions and labels only. */
export interface RunManifest {
  status: "available" | "unavailable";
  reason?: string;
  git_commit?: ManifestField<string>;
  worktree?: ManifestField<{ state: "clean" | "dirty" | "unknown"; changed_tracked_files: number | null;
    untracked_files: number | null; scope: string[] }>;
  source_ref?: ManifestField<string>;
  source_is_exact_commit?: ManifestField<boolean>;
  tasks?: ManifestField<{ schema_version: string; count: number; sha256: string; ref: string }>;
  config?: ManifestField<{ sha256: string; ref: string }>;
  protocol?: ManifestField<{ ref: string; basis: string }>;
  python?: ManifestField<{ version: string; implementation: string }>;
  platform?: ManifestField<{ system: string }>;
  adapter?: { provider: string | null; model: string | null; adapter_module: string | null;
    sdk: ManifestField<{ package: string; version: string }>; model_version: ManifestField<string> };
}

export interface LiveExperiment {
  experiment_id: string;
  status: RunStatus;
  kind: "run" | "synthetic";
  source: "live_backend";
  config: LiveConfig;
  error: { error_type: string } | null;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  updated_at: number;
  current_task: string | null;
  progress: {
    finished: number;
    started: number;
    total: number | null;
    remaining: number | null;
    fraction: number | null;
  };
  elapsed_s: number | null;
  stale: boolean;
  stale_reason: string | null;
  recording: RecordingIntegrity;
}

/** Integrity of the DASHBOARD RECORD of a run (not of the run or the agent). */
export interface RecordingIntegrity {
  status: "complete" | "incomplete" | "unavailable" | "in_progress";
  reasons: string[];
  write_failures: number;
  failures_by_hook: Record<string, number>;
  failure_types: Record<string, number>;
  authoritative_evidence: string;
}

export interface Observed {
  value: number | boolean | string | { holds: boolean | null; residual: number } | null;
  status: ObservedStatus;
  reason: string;
}

export interface TaskInfo {
  task_id: string;
  schema_version: string | null;
  family: string | null;
  declared_depth: number | null;
  depth_status: string;
  candidate_depths: Record<string, number> | null;
  difficulty: string | null;
}

export interface LiveTrial {
  run_id: string;
  task_id: string;
  seq: number | null;
  state: "running" | "finished" | "unrecorded";
  started_at: number | null;
  finished_at: number | null;
  task: TaskInfo;
  trial_status?: string;
  passed?: boolean | null;
  failure?: string;
  termination_reason?: string;
  reason_categories?: string[];
  provider_error_present?: boolean;
  fault_origin?: string;
  fault_attribution?: string;
  fault_basis?: string;
  model_requests?: number | null;
  tool_calls?: number | null;
  exec_mode?: string;
  tool_activity?: { iteration: number | null; tool: string; outcome: "ok" | "error" | "empty" }[];
  provenance?: { available: boolean; reason?: string; completeness?: Record<string, number> } &
    Record<string, unknown>;
}

export interface RateBlock {
  passed: number;
  valid: number;
  rate: number | null;
  interval: { lower: number; upper: number; confidence: number; method: string } | null;
  interval_suppressed?: string | null;
  insufficient_sample: boolean;
  notes: string[];
}

export interface Unavailable {
  available: false;
  reason: string;
}

export interface LiveAnalytics {
  /** Integrity of the dashboard record these figures come from; caveat is null only when complete. */
  recording_status?: RecordingIntegrity["status"] | null;
  recording_caveat?: string | null;
  counts: {
    trials_started: number;
    trials_finished: number;
    trials_running: number;
    trials_unrecorded?: number;
    valid: number;
    excluded: number;
    by_trial_status: Record<string, number>;
    passed: number;
    failed_valid: number;
    tasks: number;
  };
  pass_rate: RateBlock;
  failure_distribution: Record<string, number>;
  reason_categories: Record<string, number>;
  termination_reasons: Record<string, number>;
  by_task: (TaskInfo & RateBlock & { trials: number; excluded: number })[];
  by_difficulty: Record<string, RateBlock>;
  by_depth: Unavailable | { available: true; groups: Record<string, RateBlock> };
  by_family: Unavailable | { available: true; groups: Record<string, RateBlock> };
  tool_calls_total: number;
  model_requests_total: number;
  provenance_completeness: Record<string, number>;
  min_valid_for_interval: number;
}

export interface LiveDetail {
  experiment: LiveExperiment;
  trials: LiveTrial[];
  analytics: LiveAnalytics;
}

export interface LiveEvent {
  id: number;
  experiment_id: string;
  ts: number;
  type: string;
  payload: Record<string, unknown>;
}

export interface Health {
  status: string;
  api_version: string;
  server_time: number;
  uptime_s: number;
  code_revision: string | null;
  last_event_id: number;
  synthetic_runs_enabled: boolean;
  authentication: string;
  data_layers_served: string[];
}

export interface Comparison {
  kind?: "descriptive";
  interval_suppressed?: string | null;
  a: RateBlock;
  b: RateBlock;
  shared_tasks: number;
  difference: number | null;
  interval: { lower: number; upper: number; confidence: number; method: string } | null;
  insufficient_sample: boolean;
  recording?: { a: RecordingIntegrity["status"] | null; b: RecordingIntegrity["status"] | null };
  /** Per arm, present only for an arm whose recording is not known to be complete. */
  recording_caveat?: Partial<Record<"a" | "b", string>> | null;
  notes: string[];
}

export const EVENT_TYPES = [
  "experiment_queued", "experiment_running", "experiment_completed", "experiment_failed",
  "trial_started", "trial_finished",
] as const;
