import { useMemo } from "react";
import { Link } from "react-router-dom";
import type { Task } from "../../types";
import {
  JOB_ACCENT,
  JOB_LABEL,
  JobGlyph,
  jobForType,
  type JobKind,
} from "./jobStyle";
import type { OrderCountRow, PendingJob, WorkerSlot } from "./useMissionData";
import { WorkerPane } from "./WorkerPane";

export interface WorkerGridProps {
  workerSlots: WorkerSlot[];
  slots: number;
  pending: PendingJob[];
  shipped: Task[];
  faulted: Task[];
  orderCounts: OrderCountRow[];
  now: number;
  highlightedSlot?: number | null;
  director?: boolean;
  onOpen: (id: number) => void;
  onOrder: (kind?: JobKind) => void;
  onNewTaskKind: (kind: JobKind) => void;
  onCancel?: (taskId: number) => void;
}

export function WorkerGrid({
  workerSlots,
  slots,
  pending,
  shipped,
  faulted,
  orderCounts,
  now,
  highlightedSlot = null,
  director = false,
  onOpen,
  onOrder,
  onNewTaskKind,
  onCancel,
}: WorkerGridProps) {
  const runningCount = useMemo(
    () => workerSlots.filter((s) => s.task.status === "running").length,
    [workerSlots]
  );

  // Determine most-recently active running core for Director mode
  const mostRecentActiveCore = useMemo(() => {
    let targetSlot = -1;
    let maxStarted = -1;
    workerSlots.forEach((s, idx) => {
      if (s.task.status === "running") {
        const started = s.startedAtMs ?? 0;
        if (started >= maxStarted) {
          maxStarted = started;
          targetSlot = idx + 1;
        }
      }
    });
    // -1 when nothing is running: Director mode must no-op on an idle deck
    // instead of spotlighting an arbitrary core and dimming the rest.
    return targetSlot;
  }, [workerSlots]);

  const hasActiveCore = mostRecentActiveCore >= 1;

  const gaugeRadius = 8;
  const gaugeCircumference = 2 * Math.PI * gaugeRadius; // ~50.26
  const gaugeRatio = slots > 0 ? Math.min(1, runningCount / slots) : 0;
  const gaugeOffset = gaugeCircumference * (1 - gaugeRatio);

  return (
    <section
      className="surface flex min-h-0 min-w-0 flex-1 flex-col justify-between overflow-hidden px-4 py-3 font-mono"
      aria-label="Worker process grid"
    >
      {/* 1. Quick-launch presets row */}
      <div
        className="mb-2 flex items-center gap-2 overflow-x-auto pb-1 text-xs"
        role="group"
        aria-label="Quick launch presets"
      >
        <span className="shrink-0 text-[10px] uppercase tracking-wider text-ink-500 font-semibold">
          Dispatch
        </span>
        {orderCounts.map((m) => {
          const accent = JOB_ACCENT[m.kind];
          return (
            <button
              key={m.type}
              type="button"
              onClick={() => onNewTaskKind(m.kind)}
              title={`Dispatch new ${JOB_LABEL[m.kind]} (${m.type}, ${m.total} total)`}
              className="flex min-h-6 min-w-0 flex-1 cursor-pointer items-center justify-between gap-1.5 rounded-lg border border-ink-800 bg-ink-950/40 px-2 py-1 transition-colors hover:border-sky-500/50 hover:bg-ink-850"
            >
              <span className="flex items-center gap-1.5 truncate">
                <JobGlyph kind={m.kind} className={`h-3.5 w-3.5 ${accent.text}`} />
                <span className="text-ink-200 font-medium">new {JOB_LABEL[m.kind]}</span>
              </span>
              <span className="rounded bg-ink-900 px-1 py-px text-[10px] text-ink-500">
                {m.total}
              </span>
            </button>
          );
        })}
      </div>

      {/* 2. Compact Pipeline Flow Strip & Concurrency Gauge */}
      <div className="mb-2 flex items-center justify-between rounded-lg border border-ink-850/80 bg-ink-950/60 px-3 py-1.5 text-xs text-ink-400">
        <div className="flex items-center gap-2">
          {/* Queue Stage */}
          <div className="flex items-center gap-1">
            <span className="text-[10px] uppercase text-ink-500 font-semibold">Queue</span>
            <span
              className={`rounded px-1.5 py-px text-[10px] font-bold ${
                pending.length > 0 ? "bg-amber-500/20 text-amber-300" : "bg-ink-850 text-ink-500"
              }`}
            >
              {pending.length}
            </span>
          </div>

          {/* Animated SVG Flow Line 1 */}
          <svg className="h-2 w-8" viewBox="0 0 32 8" aria-hidden="true">
            <line
              x1="0"
              y1="4"
              x2="32"
              y2="4"
              stroke="#38bdf8"
              strokeWidth="2"
              className="ops-flow opacity-70"
            />
          </svg>

          {/* Cores Stage */}
          <div className="flex items-center gap-1.5">
            {/* Radial mini gauge */}
            <svg width="18" height="18" viewBox="0 0 20 20" className="shrink-0 -rotate-90">
              <circle
                cx="10"
                cy="10"
                r={gaugeRadius}
                fill="none"
                stroke="#2a2018"
                strokeWidth="2.5"
              />
              <circle
                cx="10"
                cy="10"
                r={gaugeRadius}
                fill="none"
                stroke="#38bdf8"
                strokeWidth="2.5"
                strokeDasharray={gaugeCircumference}
                strokeDashoffset={gaugeOffset}
                strokeLinecap="round"
                className="transition-all duration-500"
              />
            </svg>
            <span className="text-[10px] uppercase text-ink-500 font-semibold">Cores</span>
            <span className="font-bold text-ink-200">
              {runningCount}
              <span className="text-ink-500">/{slots}</span>
            </span>
          </div>

          {/* Animated SVG Flow Line 2 */}
          <svg className="h-2 w-8" viewBox="0 0 32 8" aria-hidden="true">
            <line
              x1="0"
              y1="4"
              x2="32"
              y2="4"
              stroke="#10b981"
              strokeWidth="2"
              className="ops-flow opacity-70"
            />
          </svg>

          {/* Shipped Stage */}
          <div className="flex items-center gap-1">
            <span className="text-[10px] uppercase text-ink-500 font-semibold">Shipped</span>
            <span className="rounded bg-emerald-500/15 px-1.5 py-px text-[10px] font-bold text-emerald-300">
              {shipped.length}
            </span>
          </div>
        </div>

        <Link
          to="/settings?section=queue"
          state={{ from: "mission" }}
          className="text-[11px] text-ink-500 hover:text-sky-300 transition-colors"
          title="Adjust worker concurrency in settings"
        >
          {slots} core{slots === 1 ? "" : "s"} allocated →
        </Link>
      </div>

      {/* 3. Pending jobs strip (when orders are pending) */}
      {pending.length > 0 && (
        <div
          className="mb-2 flex gap-2 overflow-x-auto pb-1"
          role="group"
          aria-label="Pending jobs"
        >
          {pending.map((s) => {
            const kind = jobForType(s.task.type);
            const accent = JOB_ACCENT[kind];
            const isBlocked = s.task.status === "blocked" || !!s.task.blocked;
            return (
              <div
                key={s.task.id}
                className="group relative flex shrink-0 items-center gap-2 rounded-lg border border-dashed border-ink-800 bg-ink-950/50 px-2.5 py-1 text-left transition-colors hover:border-amber-500/60"
              >
                <button
                  type="button"
                  onClick={() => onOpen(s.task.id)}
                  title={s.task.prompt}
                  className="cursor-pointer text-left flex items-center gap-2"
                >
                  <span className="font-bold text-amber-400 text-xs">#{s.task.id}</span>
                  <span className={`flex items-center gap-1 rounded px-1 text-[10px] ${accent.badge}`}>
                    <JobGlyph kind={kind} className="h-3 w-3" />
                    {JOB_LABEL[kind]}
                  </span>
                  <span className="max-w-36 truncate text-[11px] text-ink-300">
                    {s.task.prompt}
                  </span>
                  {isBlocked && (
                    <span className="rounded bg-amber-950/80 px-1 text-[9px] text-amber-400">
                      blocked
                    </span>
                  )}
                </button>
                {onCancel && (
                  <button
                    type="button"
                    title="Cancel queued order"
                    onClick={(e) => {
                      e.stopPropagation();
                      onCancel(s.task.id);
                    }}
                    className="cursor-pointer rounded px-1 text-xs text-ink-500 hover:bg-red-500/20 hover:text-red-400"
                  >
                    ✕
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* 4. Center Grid of Worker Terminal Panes */}
      <div className="grid min-h-[160px] flex-1 grid-cols-1 gap-3 overflow-y-auto pr-0.5 sm:grid-cols-2">
        {Array.from({ length: slots }, (_, i) => {
          const coreNumber = i + 1;
          const isSlotHighlighted = highlightedSlot === coreNumber;
          const isSpotlightTarget =
            director && hasActiveCore && mostRecentActiveCore === coreNumber;
          const isDimmed = director && hasActiveCore && mostRecentActiveCore !== coreNumber;

          return (
            <div
              key={i}
              className={`transition-all duration-300 ${
                isDimmed ? "opacity-60 scale-[0.985]" : "opacity-100 scale-100"
              }`}
            >
              <WorkerPane
                core={coreNumber}
                job={workerSlots[i] ?? null}
                now={now}
                compact={slots > 2}
                highlighted={isSlotHighlighted || isSpotlightTarget}
                director={director}
                onOpen={onOpen}
                onOrder={onOrder}
                onCancel={onCancel}
              />
            </div>
          );
        })}
      </div>

      {/* 5. Bottom rails for Shipped & Faulted chips */}
      {(shipped.length > 0 || (faulted && faulted.length > 0)) && (
        <div
          className="mt-2 flex flex-wrap items-center justify-between gap-2 border-t border-ink-850/80 pt-2 text-xs"
          role="group"
          aria-label="Recent outputs"
        >
          {/* Shipped rail */}
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-emerald-400">
              Shipped
            </span>
            <ul className="flex flex-wrap gap-1.5">
              {shipped.map((t) => {
                const kind = jobForType(t.type);
                return (
                  <li key={t.id}>
                    <Link
                      to={`/tasks/${t.id}`}
                      state={{ from: "mission" }}
                      title={t.prompt}
                      className="inline-flex items-center gap-1.5 rounded-full border border-emerald-900/50 bg-emerald-950/20 px-2.5 py-0.5 text-xs text-emerald-300 transition-colors hover:border-emerald-500/60 hover:text-emerald-200"
                    >
                      <span className="font-bold text-emerald-400">#{t.id}</span>
                      <JobGlyph kind={kind} className="h-3 w-3 text-emerald-400" />
                      <span className="text-ink-300">{JOB_LABEL[kind]}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>

          {/* Faulted rail */}
          {faulted && faulted.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[10px] font-semibold uppercase tracking-wider text-red-400">
                Faulted
              </span>
              <ul className="flex flex-wrap gap-1.5">
                {faulted.map((t) => {
                  const kind = jobForType(t.type);
                  return (
                    <li key={t.id}>
                      <Link
                        to={`/tasks/${t.id}`}
                        state={{ from: "mission" }}
                        title={t.prompt}
                        className="inline-flex items-center gap-1.5 rounded-full border border-red-900/50 bg-red-950/20 px-2.5 py-0.5 text-xs text-red-300 transition-colors hover:border-red-500/60 hover:text-red-200"
                      >
                        <span className="font-bold text-red-400">#{t.id}</span>
                        <JobGlyph kind={kind} className="h-3 w-3 text-red-400" />
                        <span className="text-ink-300">{JOB_LABEL[kind]}</span>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

export default WorkerGrid;
