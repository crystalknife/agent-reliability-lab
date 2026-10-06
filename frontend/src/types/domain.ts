/**
 * Domain types. Mirror the MiniLab JSONL artifacts exactly:
 * - results/<exp>.jsonl rows  (see minilab/runner.py row dict)
 * - results/<exp>_traj.jsonl  (see minilab/trajectory.py Trajectory.to_dict)
 *
 * raw_history is intentionally present on Trajectory: the exporter preserves
 * everything needed to reconstruct the visible trajectory, and the viewer
 * simply does not render the duplicated raw log.
 */

export type TrialStatus =
  | "valid"
  | "provider_error"
  | "environment_error"
  | "evaluator_error";

export type Failure =
  | "none"
  | "tool_misuse"
  | "evidence_gap"
  | "policy_misread"
  | "arithmetic_error"
  | "premature_stop"
  | "format_violation";

export type Termination =
  | "agent_final"
  | "max_steps"
  | "tool_error"
  | "invalid_action"
  | "model_error";

export interface TrialRow {
  run_id: string;
  human_run_id: string;
  experiment_id: string;
  timestamp: number;
  seed: number;
  model: string;
  provider: string;
  temperature: number;
  max_steps: number;
  task_id: string;
  difficulty: string;
  trial_status: TrialStatus;
  passed: boolean;
  failure: Failure;
  termination_reason: Termination;
  reasons: string[];
  provider_error?: string;
}

export interface TrajStep {
  step: number;
  action: string;
  arguments: Record<string, unknown>;
  observation: unknown;
}

export interface Trajectory {
  schema_version: string;
  experiment_id: string;
  run_id: string;
  timestamp: string;
  task_id: string;
  model: string;
  provider: string;
  model_version: unknown;
  temperature: number;
  seed: number;
  max_steps: number;
  prompt: string;
  prompt_hash: string;
  steps: TrajStep[];
  final_answer: unknown;
  termination_reason: Termination;
  raw_history?: unknown[];
}

export interface ManifestEntry {
  id: string;
  name: string;
  model: string;
  provider: string;
  trials: number;
  created: string;
  files: { rows: string; trajs: string };
}

export interface ExperimentBundle {
  manifest: ManifestEntry;
  rows: TrialRow[];
  trajs: Record<string, Trajectory>;
}
