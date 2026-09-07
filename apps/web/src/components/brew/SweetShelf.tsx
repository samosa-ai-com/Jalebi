/**
 * Halwai Shop — sweet shelf: today's thali, the 7-day stack shelf, the
 * menu board, and a "fresh out" ticker of recently served tasks.
 *
 * Day buckets use `updated_at` (the closest completion proxy exposed on
 * the task). The menu board keeps the shop alive when nothing fries:
 * all-time per-snack totals plus order buttons that jump to the New-task
 * form.
 */
import { useMemo } from "react";
import { Link } from "react-router-dom";
import type { Task } from "../../types";
import { snackForType, type SnackKind } from "./snacks";

function SnackGlyph({ kind }: { kind: SnackKind }) {
  if (kind === "samosa") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true" className="h-5 w-5">
        <polygon points="12,3 3,21 21,21" fill="#f7b955" stroke="#7c2d12" strokeWidth="1.5" />
        <line x1="12" y1="3" x2="12" y2="21" stroke="#7c2d12" strokeWidth="1" opacity="0.6" />
      </svg>
    );
  }
  if (kind === "pakora") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true" className="h-5 w-5">
        <circle cx="9" cy="14" r="5.5" fill="#f7b955" stroke="#7c2d12" />
        <circle cx="15.5" cy="13" r="6" fill="#f7b955" stroke="#7c2d12" />
        <circle cx="12.5" cy="9" r="4.5" fill="#f7b955" stroke="#7c2d12" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="h-5 w-5">
      <path
        d="M12 12 m0 0 c 4 0 6 2 6 4.5 c 0 3 -3.5 5 -7 4.5 c -4 -0.5 -6 -4 -4.5 -7.5 c 1.5 -4 6 -6 9.5 -4.5"
        fill="none"
        stroke="#f7b955"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

const SNACK_SINGULAR: Record<SnackKind, string> = {
  jalebi: "jalebi",
  samosa: "samosa",
  pakora: "pakora",
};

const SNACK_NAME: Record<SnackKind, string> = {
  jalebi: "jalebis",
  samosa: "samosas",
  pakora: "pakoras",
};

function dayKey(d: Date): string {
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

function parseTime(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const zoned = /([zZ]|[+-]\d{2}:?\d{2})$/.test(iso);
  const t = new Date(zoned ? iso : `${iso}Z`).getTime();
  return Number.isNaN(t) ? null : t;
}

export function SweetShelf({
  tasks,
  idle,
  onOrder,
}: {
  tasks: Task[];
  idle: boolean;
  onOrder: () => void;
}) {
  // Day-bucket aggregations depend only on the task list — memoize so the
  // 1 s parent ticker doesn't re-parse/re-sort every second.
  const { days, todayDone, perDay, served, allTime } = useMemo(() => {
    const done = tasks.filter((t) => t.status === "done");
    const today = dayKey(new Date());
    const todayDone = done.filter((t) => {
      const ts = parseTime(t.updated_at);
      return ts !== null && dayKey(new Date(ts)) === today;
    });
    const days = Array.from({ length: 7 }, (_, i) => {
      const d = new Date();
      d.setDate(d.getDate() - (6 - i));
      return d;
    });
    const perDay = days.map(
      (d) =>
        done.filter((t) => {
          const ts = parseTime(t.updated_at);
          return ts !== null && dayKey(new Date(ts)) === dayKey(d);
        }).length
    );
    const served = [...done]
      .sort((a, b) => (parseTime(b.updated_at) ?? 0) - (parseTime(a.updated_at) ?? 0))
      .slice(0, 8);
    const allTime = (["jalebi", "samosa", "pakora"] as SnackKind[]).map(
      (k) => `${done.filter((t) => snackForType(t.type) === k).length} ${SNACK_NAME[k]}`
    );
    return { days, todayDone, perDay, served, allTime };
  }, [tasks]);

  return (
    <div className="grid gap-4 lg:grid-cols-5">
      <section className="surface p-4 lg:col-span-2" aria-label="Today's thali">
        <div className="flex items-baseline justify-between">
          <h3 className="panel-title">Today&apos;s thali</h3>
          <span className="font-mono text-[11px] tabular-nums text-ink-500">
            {todayDone.length} served
          </span>
        </div>
        <svg viewBox="0 0 200 72" role="img" aria-label="Steel thali" className="mt-1 w-full">
          <ellipse
            cx="100"
            cy="42"
            rx="92"
            ry="26"
            fill="#241a10"
            stroke="#b08050"
            strokeWidth="2.5"
          />
          {/* hammered ring */}
          {Array.from({ length: 24 }, (_, i) => {
            const a = (i / 24) * Math.PI * 2;
            return (
              <circle
                key={i}
                cx={100 + 80 * Math.cos(a)}
                cy={42 + 20 * Math.sin(a)}
                r="1.3"
                fill="#4a3a2d"
              />
            );
          })}
          {/* specular arc */}
          <path
            d="M30 34 A 78 22 0 0 1 95 18"
            fill="none"
            stroke="#f7b955"
            strokeWidth="2"
            opacity="0.55"
            strokeLinecap="round"
          />
          <ellipse
            cx="100"
            cy="44"
            rx="58"
            ry="15"
            fill="#1a1410"
            stroke="#4a3a2d"
            strokeWidth="1.5"
          />
          {/* center motif */}
          <circle cx="100" cy="44" r="3" fill="none" stroke="#b08050" strokeWidth="1" />
          <circle cx="100" cy="44" r="1" fill="#b08050" />
        </svg>
        {todayDone.length === 0 ? (
          <p className="mt-1 text-center text-xs text-ink-600">
            Thali empty — the day&apos;s first fry lands here.
          </p>
        ) : (
          <div className="-mt-10 mb-1 flex min-h-9 flex-wrap items-end justify-center gap-1 px-10">
            {todayDone.slice(0, 14).map((t, i) => (
              <span
                key={t.id}
                className="brew-serve-drop drop-shadow-[0_2px_3px_rgba(0,0,0,0.6)]"
                style={{ animationDelay: `${Math.min(i, 8) * 0.06}s` }}
              >
                <SnackGlyph kind={snackForType(t.type)} />
              </span>
            ))}
            {todayDone.length > 14 && (
              <span className="font-mono text-[11px] text-ink-400">+{todayDone.length - 14}</span>
            )}
          </div>
        )}
        {served.length > 0 && (
          <div
            className="brew-ticker mt-3 overflow-hidden border-t border-ink-800/70 pt-2"
            aria-label="Fresh out of the kadhai"
          >
            <div className="brew-ticker-fast-track flex w-max gap-4">
              {[...served, ...served].map((t, i) => (
                <Link
                  key={`${t.id}-${i}`}
                  to={`/tasks/${t.id}`}
                  className="whitespace-nowrap font-mono text-[10px] text-ink-400 hover:text-syrup-300"
                  title={t.prompt}
                >
                  #{t.id} {SNACK_SINGULAR[snackForType(t.type)]} ✓
                </Link>
              ))}
            </div>
          </div>
        )}
      </section>

      <section className="surface p-4 lg:col-span-1" aria-label="Seven day shelf">
        <h3 className="panel-title">7-day shelf</h3>
        <div className="mt-2 flex h-28 items-end justify-around gap-1">
          {days.map((d, i) => {
            const shown = Math.min(5, perDay[i]);
            return (
              <div
                key={dayKey(d)}
                className="flex h-full flex-col items-center justify-end"
                title={`${perDay[i]} done`}
              >
                <span className="font-mono text-[10px] tabular-nums text-ink-400">
                  {perDay[i] > 0 ? perDay[i] : ""}
                </span>
                {perDay[i] === 0 ? (
                  <svg viewBox="0 0 24 8" aria-hidden="true" className="w-6">
                    <ellipse cx="12" cy="4" rx="11" ry="3" fill="none" stroke="#33271e" />
                  </svg>
                ) : (
                  <div className="flex flex-col-reverse items-center">
                    {Array.from({ length: shown }, (_, j) => (
                      <svg
                        key={j}
                        viewBox="0 0 24 12"
                        aria-hidden="true"
                        className="-mb-1.5 h-3.5 w-6"
                      >
                        <path
                          d="M4 8 c 2 -4 6 -5 8 -3 c 2 2 1 5 -2 5.5 c -3 0.5 -6 -1 -6 -3.5"
                          fill="none"
                          stroke={j >= 3 ? "#c2571b" : "#f7b955"}
                          strokeWidth="2"
                          strokeLinecap="round"
                        />
                      </svg>
                    ))}
                  </div>
                )}
                <span className="mt-0.5 font-mono text-[9px] uppercase text-ink-600">
                  {d.toLocaleDateString([], { weekday: "narrow" })}
                </span>
              </div>
            );
          })}
        </div>
      </section>

      <section className="surface p-4 lg:col-span-2" aria-label="Menu board">
        <h3 className="panel-title">Today&apos;s menu</h3>
        <p className="mt-2 font-mono text-xs tabular-nums text-ink-300">{allTime.join(" · ")}</p>
        <p className="text-[11px] text-ink-500">served all-time, all halls</p>
        {idle ? (
          <div className="mt-3 flex flex-wrap gap-2">
            {(["jalebi", "samosa", "pakora"] as SnackKind[]).map((k) => (
              <button
                key={k}
                type="button"
                onClick={onOrder}
                className="rounded-full border border-syrup-500/40 bg-syrup-500/10 px-3 py-1 font-mono text-[11px] text-syrup-300 transition-colors hover:bg-syrup-500/20"
              >
                Fry a {k} →
              </button>
            ))}
          </div>
        ) : (
          <p className="mt-3 text-[11px] text-ink-500">
            Orders firing — the shelf restocks as runs finish.
          </p>
        )}
      </section>
    </div>
  );
}

export default SweetShelf;
