export function shortId(id: string, n = 8): string {
  return id.length > n ? `${id.slice(0, n)}…` : id;
}

export function formatTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

export function prettyJson(v: unknown, max = 2000): string {
  const s = JSON.stringify(v, null, 2) ?? String(v);
  return s.length > max ? `${s.slice(0, max)}\n… (${s.length - max} more chars)` : s;
}

/** Circumference remainder for a [0,1] fraction. Pure math behind the dial. */
export function arcOffset(value: number, circumference: number): number {
  const v = Math.min(1, Math.max(0, value));
  return circumference * (1 - v);
}
