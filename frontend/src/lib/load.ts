import type { ExperimentBundle, ManifestEntry, Trajectory } from "../types/domain";
import { isTrajectory, isTrialRow } from "./guards";

/**
 * Base-path-aware data URLs. Never fetch relative paths: under react-router
 * deep routes (e.g. /experiments/exp-001) a relative "./data/…" would resolve
 * to /experiments/data/… and return the SPA fallback HTML instead of JSON.
 * Resolving against the origin + Vite base keeps dev, absolute hosting, and
 * subpath hosting working without route-specific hacks.
 */
export function dataUrl(file: string): string {
  const base = import.meta.env.BASE_URL || "/";
  return new URL(base.replace(/^\.\//, "/") + "data/" + file, window.location.origin).href;
}

export async function loadManifest(): Promise<ManifestEntry[]> {
  const res = await fetch(dataUrl("manifest.json"));
  if (!res.ok) throw new Error("manifest.json missing — run `npm run data` first");
  const json: unknown = await res.json();
  if (!Array.isArray(json)) throw new Error("manifest.json malformed");
  return json as ManifestEntry[];
}

export async function loadExperiment(entry: ManifestEntry): Promise<ExperimentBundle> {
  const [rowsRes, trajRes] = await Promise.all([
    fetch(dataUrl(entry.files.rows)),
    fetch(dataUrl(entry.files.trajs)),
  ]);
  if (!rowsRes.ok || !trajRes.ok) throw new Error(`artifact files missing for ${entry.id}`);
  const rowsJson: unknown = await rowsRes.json();
  const trajJson: unknown = await trajRes.json();
  if (!Array.isArray(rowsJson) || typeof trajJson !== "object" || trajJson === null) {
    throw new Error(`artifact files malformed for ${entry.id}`);
  }
  const rows = (rowsJson as unknown[]).filter(isTrialRow);
  const trajs: Record<string, Trajectory> = {};
  for (const [k, v] of Object.entries(trajJson as Record<string, unknown>)) {
    if (isTrajectory(v)) trajs[k] = v;
  }
  return { manifest: entry, rows, trajs };
}
