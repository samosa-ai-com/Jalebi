/**
 * Halwai Shop — shop floor. The karhais (cooking pots) sit center-stage in
 * a 2-column grid: queued orders wait as tickets above, finished dishes
 * land on the serving counter below. Each stove carries its own status,
 * cook, and ingredients — nothing to scroll for.
 */
import { Link } from "react-router-dom";
import type { Task } from "../../types";
import { FryStation, type StationTask } from "./FryStation";
import { snackForType, type SnackKind } from "./snacks";

export interface MenuRow {
  type: string;
  total: number;
}

export function ShopFloor({
  stations,
  slots,
  served,
  spoiled = [],
  menu,
  now,
  highlightedSlot = null,
  onOpen,
  onOrder,
  onNewTaskKind,
  onCancel,
}: {
  stations: StationTask[];
  slots: number;
  /** Most recent done tasks (max 3) for the serving counter. */
  served: Task[];
  /** Most recent failed/interrupted tasks (max 3) for the spoiled counter. */
  spoiled?: Task[];
  menu: MenuRow[];
  now: number;
  highlightedSlot?: number | null;
  onOpen: (id: number) => void;
  onOrder: () => void;
  onNewTaskKind: (kind: SnackKind) => void;
  onCancel?: (taskId: number) => void;
}) {
  const queued = stations.filter((s) => s.task.status === "queued");
  return (
    <section
      className="surface flex min-h-0 min-w-0 flex-col justify-between overflow-hidden px-4 py-3"
      aria-label="Karhais (running tasks)"
    >
      <div
        className="mb-2 flex items-center gap-1.5 overflow-x-auto"
        role="group"
        aria-label="Today's menu"
      >
        <span className="shrink-0 font-mono text-[10px] uppercase tracking-wide text-ink-500">
          Today&apos;s menu
        </span>
        {menu.map((m) => (
          <button
            key={m.type}
            type="button"
            onClick={() => onNewTaskKind(snackForType(m.type))}
            title={`Order a ${snackForType(m.type)} (${m.type}, ${m.total} total)`}
            className="flex min-w-0 flex-1 cursor-pointer items-center gap-0.5 rounded-full border border-ink-800 px-1 py-0.5 transition-colors hover:border-syrup-500/60 hover:bg-syrup-500/10"
          >
            <span className="truncate font-mono text-[10px] text-ink-200">
              {snackForType(m.type)}
            </span>
            <span className="shrink-0 font-mono text-[9px] text-ink-500">
              {m.type} · {m.total}
            </span>
          </button>
        ))}
      </div>

      <div className="flex items-baseline justify-between gap-2">
        <h3 className="panel-title">Karhais (running tasks)</h3>
        <Link
          to="/settings?section=queue"
          className="font-mono text-[11px] text-ink-500 hover:text-syrup-300"
          title="Worker slots come from the queue concurrency setting"
        >
          {slots} slot{slots === 1 ? "" : "s"} →
        </Link>
      </div>

      {queued.length > 0 && (
        <div
          className="mt-2 flex gap-1.5 overflow-x-auto pb-1"
          role="group"
          aria-label="Queued tasks"
        >
          {queued.map((s) => (
            <div
              key={s.task.id}
              className="group relative flex shrink-0 items-center gap-1.5 rounded-lg border border-dashed border-ink-700 bg-ink-950/30 px-2 py-1 text-left transition-colors hover:border-syrup-500/60"
            >
              <button
                type="button"
                onClick={() => onOpen(s.task.id)}
                title={s.task.prompt}
                className="cursor-pointer text-left"
              >
                <div className="flex items-center gap-1">
                  <span className="font-mono text-[11px] text-syrup-400">#{s.task.id}</span>{" "}
                  <span className="font-mono text-[10px] text-ink-500">
                    {snackForType(s.task.type)}
                  </span>
                  {s.task.blocked && (
                    <span className="rounded bg-chai-500/20 px-1 py-px font-mono text-[9px] text-chai-300">
                      blocked
                    </span>
                  )}
                </div>
                <span className="block max-w-44 truncate text-[11px] text-ink-300">
                  {s.task.prompt}
                </span>
              </button>
              {onCancel && (
                <button
                  type="button"
                  title="Cancel queued order"
                  onClick={(e) => {
                    e.stopPropagation();
                    onCancel(s.task.id);
                  }}
                  className="hidden group-hover:inline-flex cursor-pointer rounded px-1 py-0.5 font-mono text-[10px] text-ink-500 hover:bg-red-500/10 hover:text-red-300"
                >
                  ✕
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="mt-2 grid min-h-[120px] flex-1 grid-cols-1 gap-3 overflow-y-auto pr-0.5 sm:grid-cols-2">
        {Array.from({ length: slots }, (_, i) => (
          <FryStation
            key={i}
            slot={i + 1}
            station={stations[i] ?? null}
            now={now}
            compact={slots > 2}
            highlighted={highlightedSlot === i + 1}
            onOpen={onOpen}
            onOrder={onOrder}
            onCancel={onCancel}
          />
        ))}
      </div>

      {(served.length > 0 || (spoiled && spoiled.length > 0)) && (
        <div
          className="mt-2 flex flex-wrap items-center justify-between gap-2 border-t border-ink-800/60 pt-2"
          role="group"
          aria-label="Recent tasks"
        >
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="font-mono text-[10px] uppercase tracking-wide text-ink-500">Served</span>
            <ul className="flex flex-wrap gap-1.5">
              {served.map((t) => (
                <li key={t.id}>
                  <Link
                    to={`/tasks/${t.id}`}
                    state={{ from: "mission" }}
                    title={t.prompt}
                    className="inline-block rounded-full border border-ink-800 px-2 py-0.5 font-mono text-[11px] text-ink-300 transition-colors hover:border-syrup-500/60 hover:text-syrup-300"
                  >
                    <span className="text-syrup-400">#{t.id}</span> {snackForType(t.type)}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
          {spoiled && spoiled.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="font-mono text-[10px] uppercase tracking-wide text-red-400/80">Spoiled (did not finish)</span>
              <ul className="flex flex-wrap gap-1.5">
                {spoiled.map((t) => (
                  <li key={t.id}>
                    <Link
                      to={`/tasks/${t.id}`}
                      state={{ from: "mission" }}
                      title={t.prompt}
                      className="inline-block rounded-full border border-red-900/40 bg-red-950/20 px-2 py-0.5 font-mono text-[11px] text-red-300/80 transition-colors hover:border-red-500/60 hover:text-red-200"
                    >
                      <span className="text-red-400">#{t.id}</span> {snackForType(t.type)}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

export default ShopFloor;
