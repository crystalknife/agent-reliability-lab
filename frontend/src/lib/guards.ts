/**
 * Runtime type guards for experiment artifacts. Hand-rolled instead of zod:
 * one small file, zero dependencies, same guarantee.
 */
import type { Failure, Termination, Trajectory, TrialRow, TrialStatus, TrajStep } from "../types/domain";

const STATUSES: TrialStatus[] = ["valid", "provider_error", "environment_error", "evaluator_error"];
const FAILURES: Failure[] = [
  "none", "tool_misuse", "evidence_gap", "policy_misread",
  "arithmetic_error", "premature_stop", "format_violation",
];
const TERMS: Termination[] = ["agent_final", "max_steps", "tool_error", "invalid_action", "model_error"];

function isRec(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}

export function isTrialRow(v: unknown): v is TrialRow {
  if (!isRec(v)) return false;
  return (
    typeof v.run_id === "string" &&
    typeof v.human_run_id === "string" &&
    typeof v.task_id === "string" &&
    typeof v.model === "string" &&
    typeof v.provider === "string" &&
    typeof v.seed === "number" &&
    typeof v.passed === "boolean" &&
    STATUSES.includes(v.trial_status as TrialStatus) &&
    FAILURES.includes(v.failure as Failure) &&
    TERMS.includes(v.termination_reason as Termination) &&
    Array.isArray(v.reasons)
  );
}

export function isTrajStep(v: unknown): v is TrajStep {
  return (
    isRec(v) &&
    typeof v.step === "number" &&
    typeof v.action === "string" &&
    isRec(v.arguments)
  );
}

export function isTrajectory(v: unknown): v is Trajectory {
  if (!isRec(v)) return false;
  return (
    typeof v.run_id === "string" &&
    typeof v.task_id === "string" &&
    Array.isArray(v.steps) &&
    v.steps.every(isTrajStep) &&
    TERMS.includes(v.termination_reason as Termination)
  );
}
