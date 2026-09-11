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

export function OpsStats({ tasks, now }: { tasks: Task[]; now: number }) {
  const stats = useMemo(() => {
    const today = dayKey(now);
    let shippedToday = 0;
    let faultedToday = 0;
    let needsYou = 0;
    let active = 0;
    const runTimes: number[] = [];
    const shippedByDay = new Map<string, number>();

    for (const t of tasks) {
      if (t.attention === "needs_you") needsYou += 1;
      if (t.status === "queued" || t.status === "running") active += 1;
      const updated = parseTime(t.updated_at);
      const updatedDay = updated === null ? null : dayKey(updated);

      if (t.status === "done" && updated !== null && updatedDay !== null) {
        shippedByDay.set(updatedDay, (shippedByDay.get(updatedDay) ?? 0) + 1);
        if (updatedDay === today) {
          shippedToday += 1;
          const created = parseTime(t.created_at);
          if (created !== null && updated > created) runTimes.push(updated - created);
        }
      }

      if (
        (t.status === "failed" || t.status === "timed_out" || t.status === "interrupted") &&
        updatedDay === today
      ) {
        faultedToday += 1;
      }
    }

    const bars: { key: string; label: string; count: number }[] = [];
    for (let i = 6; i >= 0; i -= 1) {
      const d = new Date(now - i * 86_400_000);
      const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
      bars.push({
        key,
        label: d.toLocaleDateString([], { weekday: "narrow" }),
        count: shippedByDay.get(key) ?? 0,
      });
    }

    const max = Math.max(1, ...bars.map((b) => b.count));
    const avg = runTimes.length > 0 ? runTimes.reduce((a, b) => a + b, 0) / runTimes.length : null;

    return { shippedToday, faultedToday, needsYou, active, bars, max, avg };
  }, [tasks, now]);

  return (
    <section
      className="surface min-w-0 overflow-hidden px-3 py-2 font-mono"
      aria-label="Deck statistics"
    >
      <div className="flex items-baseline justify-between">
        <h3 className="panel-title text-xs uppercase tracking-wider text-ink-300">Metrics</h3>
        <span className="text-[10px] text-ink-500">24h telemetry</span>
      </div>

      <dl className="mt-1.5 grid grid-cols-4 gap-2">
        <div className="min-w-0">
          <dt className="text-[9px] uppercase tracking-wide text-ink-500">shipped</dt>
          <dd className="text-lg font-bold tabular-nums text-emerald-400">{stats.shippedToday}</dd>
        </div>

        <div className="min-w-0">
          <dt className="text-[9px] uppercase tracking-wide text-ink-500">needs you</dt>
          <dd
            className={`text-lg font-bold tabular-nums ${
              stats.needsYou > 0 ? "text-amber-300 animate-pulse" : "text-ink-100"
            }`}
          >
            {stats.needsYou}
          </dd>
        </div>

        <div className="min-w-0">
          <dt className="text-[9px] uppercase tracking-wide text-ink-500">faulted</dt>
          <dd
            className={`text-lg font-bold tabular-nums ${
              stats.faultedToday > 0 ? "text-red-400" : "text-ink-100"
            }`}
          >
            {stats.faultedToday}
          </dd>
        </div>

        <div className="min-w-0">
          <dt className="text-[9px] uppercase tracking-wide text-ink-500">avg runtime</dt>
          <dd className="text-lg font-bold tabular-nums text-sky-400">
            {stats.avg === null ? "—" : formatMinutes(stats.avg)}
          </dd>
        </div>
      </dl>

      <h4 className="mt-2 text-[9px] uppercase tracking-wide text-ink-500">
        Shipped · last 7 days
      </h4>

      <div
        className="mt-1 flex h-10 items-stretch gap-1.5"
        role="img"
        aria-label={`Tasks shipped per day: ${stats.bars.map((b) => `${b.label} ${b.count}`).join(", ")}`}
      >
        {stats.bars.map((b) => (
          <div
            key={b.key}
            className="flex min-w-0 flex-1 flex-col items-center justify-end gap-1"
            title={`${b.count} shipped`}
          >
            <div
              className={`w-full rounded-sm transition-all duration-500 ease-out ${
                b.count > 0 ? "bg-sky-500/80 hover:bg-sky-400" : "bg-ink-850"
              }`}
              style={{ height: `${Math.max(8, Math.round((b.count / stats.max) * 100))}%` }}
            />
            <span className="text-[9px] text-ink-500">{b.label}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

export default OpsStats;
