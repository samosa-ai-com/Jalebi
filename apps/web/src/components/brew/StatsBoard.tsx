/**
 * Halwai Shop — stats board. Real numbers, no shop analogy: today's
 * throughput plus a 7-day CSS bar chart of completed tasks.
 */
import { useMemo } from "react";
import type { Task } from "../../types";

function parseTime(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const zoned = /([zZ]|[+-]\d{2}:?\d{2})$/.test(iso);
  const t = new Date(zoned ? iso : `${iso}Z`).getTime();
  return Number.isNaN(t) ? null : t;
}

function dayKey(ms: number): string {
  const d = new Date(ms);
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

function formatMinutes(ms: number): string {
  const m = Math.round(ms / 60_000);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function StatsBoard({ tasks, now }: { tasks: Task[]; now: number }) {
  const stats = useMemo(() => {
    const today = dayKey(now);
    let doneToday = 0;
    let failedToday = 0;
    let needsYou = 0;
    let active = 0;
    const fryTimes: number[] = [];
    const doneByDay = new Map<string, number>();

    for (const t of tasks) {
      if (t.attention === "needs_you") needsYou += 1;
      if (t.status === "queued" || t.status === "running") active += 1;
      const updated = parseTime(t.updated_at);
      const updatedDay = updated === null ? null : dayKey(updated);
      if (t.status === "done" && updated !== null && updatedDay !== null) {
        doneByDay.set(updatedDay, (doneByDay.get(updatedDay) ?? 0) + 1);
        if (updatedDay === today) {
          doneToday += 1;
          const created = parseTime(t.created_at);
          if (created !== null && updated > created) fryTimes.push(updated - created);
        }
      }
      if (
        (t.status === "failed" || t.status === "timed_out" || t.status === "interrupted") &&
        updatedDay === today
      ) {
        failedToday += 1;
      }
    }

    const bars: { key: string; label: string; count: number }[] = [];
    for (let i = 6; i >= 0; i -= 1) {
      const d = new Date(now - i * 86_400_000);
      const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
      bars.push({
        key,
        label: d.toLocaleDateString([], { weekday: "narrow" }),
        count: doneByDay.get(key) ?? 0,
      });
    }
    const max = Math.max(1, ...bars.map((b) => b.count));
    const avg = fryTimes.length > 0 ? fryTimes.reduce((a, b) => a + b, 0) / fryTimes.length : null;

    return { doneToday, failedToday, needsYou, active, bars, max, avg };
  }, [tasks, now]);

  return (
    <section className="surface px-3 py-2" aria-label="Shop stats">
      <h3 className="panel-title">Today</h3>
      <dl className="mt-1.5 grid grid-cols-4 gap-2">
        <div>
          <dt className="font-mono text-[10px] uppercase tracking-wide text-ink-500">served</dt>
          <dd className="font-mono text-lg tabular-nums text-ink-100">{stats.doneToday}</dd>
        </div>
        <div>
          <dt className="font-mono text-[10px] uppercase tracking-wide text-ink-500">needs you</dt>
          <dd
            className={`font-mono text-lg tabular-nums ${stats.needsYou > 0 ? "text-syrup-300" : "text-ink-100"}`}
          >
            {stats.needsYou}
          </dd>
        </div>
        <div>
          <dt className="font-mono text-[10px] uppercase tracking-wide text-ink-500">spoiled</dt>
          <dd className="font-mono text-lg tabular-nums text-ink-100">{stats.failedToday}</dd>
        </div>
        <div>
          <dt className="font-mono text-[10px] uppercase tracking-wide text-ink-500">avg fry</dt>
          <dd className="font-mono text-lg tabular-nums text-ink-100">
            {stats.avg === null ? "—" : formatMinutes(stats.avg)}
          </dd>
        </div>
      </dl>
      <h4 className="mt-2 font-mono text-[10px] uppercase tracking-wide text-ink-500">
        Served · last 7 days
      </h4>
      <div
        className="mt-1 flex h-10 items-end gap-1.5"
        role="img"
        aria-label={`Tasks served per day: ${stats.bars.map((b) => `${b.label} ${b.count}`).join(", ")}`}
      >
        {stats.bars.map((b) => (
          <div
            key={b.key}
            className="flex min-w-0 flex-1 flex-col items-center gap-1"
            title={`${b.count} served`}
          >
            <div
              className={`w-full rounded-sm ${b.count > 0 ? "bg-syrup-500/70" : "bg-ink-800"}`}
              style={{ height: `${Math.max(8, Math.round((b.count / stats.max) * 100))}%` }}
            />
            <span className="font-mono text-[9px] text-ink-500">{b.label}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

export default StatsBoard;
