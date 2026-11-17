/**
 * Client for the live backend. Same-origin `/api/*` only: in development Vite
 * proxies it to the local Python server; on the public static site there is
 * no backend and every call fails, which the UI reports as "offline".
 */
import * as React from "react";
import type { Comparison, Health, LiveDetail, LiveEvent, LiveExperiment } from "./types";
import { EVENT_TYPES } from "./types";

export type Connection = "connecting" | "live" | "reconnecting" | "offline";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function getJson<T>(path: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { headers: { Accept: "application/json" } });
  } catch {
    throw new ApiError(0, "live backend not reachable");
  }
  const type = res.headers?.get?.("content-type") ?? "application/json";
  if (!res.ok || !type.includes("json")) {
    // A static host answers /api/* with index.html (200, text/html): that is "no backend".
    throw new ApiError(res.ok ? 0 : res.status, res.ok ? "live backend not reachable" : `HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => getJson<Health>("/api/health"),
  experiments: () => getJson<{ experiments: LiveExperiment[]; server_time: number }>("/api/experiments"),
  experiment: (id: string) => getJson<LiveDetail>(`/api/experiments/${encodeURIComponent(id)}`),
  events: (after: number) => getJson<{ events: LiveEvent[] }>(`/api/events?after=${after}`),
  compare: (a: string, b: string) =>
    getJson<{ comparison: Comparison }>(`/api/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`),
  async startSynthetic(body: Record<string, unknown>): Promise<{ experiment_id: string }> {
    const res = await fetch("/api/dev/synthetic-run", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-ARL-Local": "1" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new ApiError(res.status, `HTTP ${res.status}`);
    return (await res.json()) as { experiment_id: string };
  },
};

export const RETRY_DELAYS_MS = [1000, 2000, 5000, 10000];
const MAX_EVENTS = 200;

interface FeedState {
  connection: Connection;
  events: LiveEvent[];
  lastEventId: number;
  /** Increments on every received event; pages refetch when it changes. */
  tick: number;
  attempts: number;
}

/**
 * Live event feed over Server-Sent Events with explicit reconnection.
 * The browser's own EventSource retry is not used: we close on error and
 * reopen with `?after=<last id>` so the status shown to the user is accurate
 * and no event is skipped or replayed.
 */
export function useLiveFeed(enabled = true): FeedState {
  const [state, setState] = React.useState<FeedState>({
    connection: "connecting", events: [], lastEventId: 0, tick: 0, attempts: 0,
  });
  const lastId = React.useRef(0);

  React.useEffect(() => {
    if (!enabled) return;
    let closed = false;
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let attempts = 0;

    const onEvent = (raw: MessageEvent) => {
      let ev: LiveEvent;
      try {
        ev = JSON.parse(raw.data as string) as LiveEvent;
      } catch {
        return;
      }
      if (typeof ev.id !== "number" || ev.id <= lastId.current) return; // never replay
      lastId.current = ev.id;
      setState((s) => ({
        ...s, connection: "live", lastEventId: ev.id, tick: s.tick + 1,
        events: [ev, ...s.events].slice(0, MAX_EVENTS),
      }));
    };

    const schedule = () => {
      if (closed) return;
      const delay = RETRY_DELAYS_MS[Math.min(attempts, RETRY_DELAYS_MS.length - 1)];
      attempts += 1;
      setState((s) => ({ ...s, connection: attempts > 3 ? "offline" : "reconnecting", attempts }));
      timer = setTimeout(open, delay);
    };

    const open = () => {
      if (closed) return;
      if (typeof EventSource === "undefined") {
        setState((s) => ({ ...s, connection: "offline" }));
        return;
      }
      source = new EventSource(`/api/stream?after=${lastId.current}`);
      source.onopen = () => {
        attempts = 0;
        // tick: a (re)connection may follow missed updates, so pages refetch.
        setState((s) => ({ ...s, connection: "live", attempts: 0, tick: s.tick + 1 }));
      };
      source.onerror = () => {
        source?.close();
        source = null;
        schedule();
      };
      for (const t of EVENT_TYPES) source.addEventListener(t, onEvent as EventListener);
    };

    open();
    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      source?.close();
    };
  }, [enabled]);

  return state;
}

export interface Loaded<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
}

/** Fetch on mount and whenever `tick` changes. Keeps the last good data on a failed refresh. */
export function useLive<T>(load: () => Promise<T>, tick: number, key = ""): Loaded<T> {
  const [state, setState] = React.useState<Loaded<T>>({ data: null, error: null, loading: true });
  const loadRef = React.useRef(load);
  loadRef.current = load;
  React.useEffect(() => {
    let alive = true;
    loadRef.current()
      .then((data) => alive && setState({ data, error: null, loading: false }))
      .catch((e: unknown) => {
        const error = e instanceof ApiError ? e : new ApiError(0, String(e));
        if (alive) setState((s) => ({ data: s.data, error, loading: false }));
      });
    return () => {
      alive = false;
    };
  }, [tick, key]);
  return state;
}
