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
      <svg viewBox="0 0 24 24" aria-hidden="true" className="h-6 w-6">
        <polygon points="12,3 3,21 21,21" fill="#f7b955" stroke="#7c2d12" strokeWidth="1.5" />
        <line x1="12" y1="3" x2="12" y2="21" stroke="#7c2d12" strokeWidth="1" opacity="0.6" />
      </svg>
    );
  }
  if (kind === "pakora") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true" className="h-6 w-6">
        <circle cx="9" cy="14" r="5.5" fill="#f7b955" stroke="#7c2d12" />
        <circle cx="15.5" cy="13" r="6" fill="#f7b955" stroke="#7c2d12" />
        <circle cx="12.5" cy="9" r="4.5" fill="#f7b955" stroke="#7c2d12" />
      </svg>
    );
  }
  // Double interlocking spiral — a real jalebi reads as two connected
  // coils, not a single loop. Sized up so it stays legible on the thali.
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="h-7 w-7">
      <path
        d="M 9.5 7.5 C 5.5 8 4 12 6.5 15.5 C 9 19 14 18 17 15.5 C 19.5 13 19 8.5 15.5 7 C 12 5.5 9 8 11.5 12 C 13.5 15 17 14 17.5 11"
        fill="none"
        stroke="#522003"
        strokeWidth="3.4"
        opacity="0.7"
        strokeLinecap="round"
      />
      <path
        d="M 8.5 14.5 C 5.5 13.5 5 9.5 8 7.5 C 11 5.5 14 6 15.5 8.5 C 17 11 16 15 13.5 16.5 C 10.5 18 7.5 16.5 7 13 C 6.5 9 10 7 13.5 8.5 C 17 10 18.5 14 16 16.5 C 14 18.5 10.5 17.5 11 14.5 C 11.5 12 14 11.5 15 12.5"
        fill="none"
        stroke="#ef831e"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M 8 7.5 C 10.5 5.8 13.5 6.2 15 8"
        fill="none"
        stroke="#fed776"
        strokeWidth="0.8"
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

/** Engineering legend: which snack fries which task type. */
const MENU: { kind: SnackKind; taskType: string; blurb: string }[] = [
  { kind: "jalebi", taskType: "freeform", blurb: "open-ended builds" },
  { kind: "samosa", taskType: "issue_fix", blurb: "bug squashes" },
  { kind: "pakora", taskType: "pr_review", blurb: "code reviews" },
];

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
  onOrder: (kind?: SnackKind) => void;
}) {
  // Day-bucket aggregations depend only on the task list — memoize so the
  // 1 s parent ticker doesn't re-parse/re-sort every second.
  const { days, todayDone, perDay, served, menuCounts } = useMemo(() => {
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
    const menuCounts = MENU.map((m) => ({
      ...m,
      count: done.filter((t) => snackForType(t.type) === m.kind).length,
    }));
    return { days, todayDone, perDay, served, menuCounts };
  }, [tasks]);

  return (
    <div className="grid gap-4 lg:grid-cols-5">
      <section className="surface px-3 py-2.5 lg:col-span-2" aria-label="Today's thali">
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

      <section className="surface px-3 py-2.5 lg:col-span-1" aria-label="Seven day shelf">
        <h3 className="panel-title">7-day shelf</h3>
        <div className="mt-1.5 flex h-16 items-end justify-around gap-1">
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

      <section className="surface px-3 py-2.5 lg:col-span-2" aria-label="Menu board">
        <div className="flex items-baseline justify-between">
          <h3 className="panel-title">Today&apos;s menu</h3>
          <span className="font-mono text-[10px] text-ink-600">snack → task type</span>
        </div>
        <ul className="mt-2 space-y-1.5">
          {menuCounts.map((m) => (
            <li key={m.kind} className="flex items-center gap-2 text-xs">
              <SnackGlyph kind={m.kind} />
              <span className="font-mono tabular-nums text-ink-100">{m.count}×</span>
              <span className="font-mono text-syrup-300">{m.taskType}</span>
              <span className="truncate text-[11px] text-ink-500">{m.blurb}</span>
            </li>
          ))}
        </ul>
        {idle ? (
          <div className="mt-3 flex flex-wrap gap-2">
            {MENU.map((m) => (
              <button
                key={m.kind}
                type="button"
                onClick={() => onOrder(m.kind)}
                title={`Start a ${m.taskType} task`}
                className="rounded-full border border-syrup-500/40 bg-syrup-500/10 px-3 py-1 font-mono text-[11px] text-syrup-300 transition-colors hover:bg-syrup-500/20"
              >
                New {m.taskType} →
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
