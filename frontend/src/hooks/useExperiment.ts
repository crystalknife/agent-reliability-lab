import * as React from "react";
import type { ExperimentBundle, ManifestEntry } from "../types/domain";
import { loadExperiment, loadManifest } from "../lib/load";

export function useManifest() {
  const [entries, setEntries] = React.useState<ManifestEntry[] | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  React.useEffect(() => {
    loadManifest().then(setEntries).catch((e: unknown) => setError(String(e)));
  }, []);
  return { entries, error };
}

export function useExperiment(id: string | undefined) {
  const [bundle, setBundle] = React.useState<ExperimentBundle | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  React.useEffect(() => {
    setBundle(null);
    setError(null);
    if (!id) return;
    let live = true;
    loadManifest()
      .then((entries) => {
        const entry = entries.find((e) => e.id === id);
        if (!entry) throw new Error(`unknown experiment ${id}`);
        return loadExperiment(entry);
      })
      .then((b) => {
        if (live) setBundle(b);
      })
      .catch((e: unknown) => {
        if (live) setError(String(e));
      });
    return () => {
      live = false;
    };
  }, [id]);
  return { bundle, error };
}
