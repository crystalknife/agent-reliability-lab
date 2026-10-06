/**
 * Export MiniLab JSONL artifacts to static JSON for the frontend.
 * Read-only over ../../results: streams line-delimited files, validates
 * minimal row shape, preserves every field (nothing dropped for convenience).
 * Usage: npm run data [-- results/file.jsonl ...]  (default: all known experiments)
 */
import { createReadStream, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createInterface } from "node:readline";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const OUT = join(dirname(fileURLToPath(import.meta.url)), "..", "public", "data");

// Stem -> { rows file, trajs file }. Explicit registry: new experiments are
// added here deliberately, never auto-globbed (avoids publishing strays).
const EXPERIMENTS = {
  "exp-001": {
    name: "EXP-001 · Local Qwen3-4B Baseline",
    rows: "results/local_qwen3_8task_2x.jsonl",
    trajs: "results/local_qwen3_8task_2x_traj.jsonl",
  },
};

function prettyName(id) {
  return id.replace(/[-_]+/g, " ");
}

async function readLines(path) {
  const rows = [];
  const rl = createInterface({ input: createReadStream(path), crlfDelay: Infinity });
  for await (const line of rl) {
    const t = line.trim();
    if (t) rows.push(JSON.parse(t));
  }
  return rows;
}

async function main() {
  mkdirSync(OUT, { recursive: true });
  const manifest = [];
  const only = new Set(process.argv.slice(2));
  for (const [id, cfg] of Object.entries(EXPERIMENTS)) {
    if (only.size > 0 && !only.has(cfg.rows)) continue;
    const rowsPath = join(ROOT, cfg.rows);
    const trajPath = join(ROOT, cfg.trajs);
    if (!existsSync(rowsPath) || !existsSync(trajPath)) {
      console.warn(`skip ${id}: missing artifact files`);
      continue;
    }
    const rows = await readLines(rowsPath);
    const trajs = await readLines(trajPath);
    if (!rows.every((r) => typeof r.run_id === "string" && typeof r.task_id === "string")) {
      throw new Error(`${id}: rows failed minimal shape check`);
    }
    const byRun = {};
    for (const t of trajs) {
      if (typeof t.run_id === "string") byRun[t.run_id] = t;
    }
    const first = rows[0] ?? {};
    const created = new Date(
      Math.min(...rows.map((r) => (r.timestamp ?? Date.now() / 1000) * 1000)),
    ).toISOString().slice(0, 10);
    manifest.push({
      id,
      name: cfg.name ?? prettyName(id),
      model: first.model ?? "?",
      provider: first.provider ?? "?",
      trials: rows.length,
      created,
      files: { rows: `${id}.json`, trajs: `${id}.traj.json` },
    });
    writeFileSync(join(OUT, `${id}.json`), JSON.stringify(rows));
    writeFileSync(join(OUT, `${id}.traj.json`), JSON.stringify(byRun));
    console.log(`${id}: ${rows.length} rows, ${Object.keys(byRun).length} trajectories`);
  }
  if (only.size === 0) {
    writeFileSync(join(OUT, "manifest.json"), JSON.stringify(manifest, null, 2));
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
