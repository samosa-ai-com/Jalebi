/** Phase 4 T4.4 — live "running now" view helpers. */

/** Build an inline SVG sparkline (40×16) for the RunningCard. */
import type { Step } from "../types";

export function buildActivityBars(
  steps: Step[],
  buckets: number = 40
): number[] {
  const counts: number[] = new Array(buckets).fill(0);
  const toolSteps = steps.filter((s) => s.type === "tool_call");
  if (toolSteps.length === 0) {
    return counts;
  }
  // Determine time bounds: earliest step ts → now (or last step ts).
  const tsValues = toolSteps
    .map((s) => new Date(s.ts.endsWith("Z") ? s.ts : `${s.ts}Z`).getTime())
    .filter((t) => !Number.isNaN(t));
  if (tsValues.length === 0) {
    return counts;
  }
  const minTs = Math.min(...tsValues);
  const maxTs = Math.max(...tsValues, Date.now());
  const span = maxTs - minTs;
  if (span <= 0) {
    counts[buckets - 1] = toolSteps.length;
    return counts;
  }
  for (const s of toolSteps) {
    const t = new Date(s.ts.endsWith("Z") ? s.ts : `${s.ts}Z`).getTime();
    if (Number.isNaN(t)) continue;
    const idx = Math.min(
      buckets - 1,
      Math.max(0, Math.floor(((t - minTs) / span) * (buckets - 1)))
    );
    counts[idx] += 1;
  }
  return counts;
}

export function formatElapsed(startedAt: string | null): string {
  if (!startedAt) return "—";
  const start = new Date(startedAt.endsWith("Z") ? startedAt : `${startedAt}Z`).getTime();
  if (Number.isNaN(start)) return "—";
  const sec = Math.max(0, Math.round((Date.now() - start) / 1000));
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m}m ${sec % 60}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

export function lastMessageText(steps: Step[]): string | null {
  for (let i = steps.length - 1; i >= 0; i -= 1) {
    const s = steps[i];
    if (s.type === "message" && s.text && s.text.trim()) {
      const t = (s.text || "").replace(/\s+/g, " ").trim();
      return t.length > 120 ? `${t.slice(0, 120)}…` : t;
    }
  }
  return null;
}