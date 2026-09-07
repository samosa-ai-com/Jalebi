/**
 * Halwai Shop — shop floor. The karhais (cooking pots) sit center-stage in
 * a 2-column grid: queued orders wait as tickets above, finished dishes
 * land on the serving counter below. Each stove carries its own status,
 * cook, and ingredients — nothing to scroll for.
 */
import { Link } from "react-router-dom";
import type { Task } from "../../types";
import { FryStation, type StationTask } from "./FryStation";
import { snackForType } from "./snacks";

export function ShopFloor({
  stations,
  slots,
  served,
  now,
  onOpen,
  onOrder,
}: {
  stations: StationTask[];
  slots: number;
  /** Most recent done tasks (max 3) for the serving counter. */
  served: Task[];
  now: number;
  onOpen: (id: number) => void;
  onOrder: () => void;
}) {
  const queued = stations.filter((s) => s.task.status === "queued");
  return (
    <section
      className="surface flex min-h-0 flex-col px-4 py-3"
      aria-label="Karhais (cooking pots)"
    >
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="panel-title">Karhais (cooking pots)</h3>
        <Link
          to="/settings?section=queue"
          className="font-mono text-[11px] text-ink-500 hover:text-syrup-300"
          title="Worker slots come from the queue concurrency setting"
        >
          {slots} burner{slots === 1 ? "" : "s"} →
        </Link>
      </div>

      {queued.length > 0 && (
        <div
          className="mt-2 flex gap-1.5 overflow-x-auto pb-1"
          role="group"
          aria-label="Order tickets"
        >
          {queued.map((s) => (
            <button
              key={s.task.id}
              type="button"
              onClick={() => onOpen(s.task.id)}
              title={s.task.prompt}
              className="shrink-0 cursor-pointer rounded-lg border border-dashed border-ink-700 px-2 py-1 text-left transition-colors hover:border-syrup-500/60"
            >
              <span className="font-mono text-[11px] text-syrup-400">#{s.task.id}</span>{" "}
              <span className="font-mono text-[10px] text-ink-500">
                {snackForType(s.task.type)}
              </span>
              <span className="block max-w-44 truncate text-[11px] text-ink-300">
                {s.task.prompt}
              </span>
            </button>
          ))}
        </div>
      )}

      <div className="mt-2 grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-y-auto pr-0.5 sm:grid-cols-2">
        {Array.from({ length: slots }, (_, i) => (
          <FryStation
            key={i}
            slot={i + 1}
            station={stations[i] ?? null}
            now={now}
            onOpen={onOpen}
            onOrder={onOrder}
          />
        ))}
      </div>

      {served.length > 0 && (
        <div
          className="mt-2 border-t border-ink-800/60 pt-2"
          role="group"
          aria-label="Serving counter"
        >
          <p className="font-mono text-[10px] uppercase tracking-wide text-ink-500">Served</p>
          <ul className="mt-1 flex flex-wrap gap-1.5">
            {served.map((t) => (
              <li key={t.id}>
                <Link
                  to={`/tasks/${t.id}`}
                  title={t.prompt}
                  className="inline-block rounded-full border border-ink-800 px-2 py-0.5 font-mono text-[11px] text-ink-300 transition-colors hover:border-syrup-500/60 hover:text-syrup-300"
                >
                  <span className="text-syrup-400">#{t.id}</span> {snackForType(t.type)}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

export default ShopFloor;
