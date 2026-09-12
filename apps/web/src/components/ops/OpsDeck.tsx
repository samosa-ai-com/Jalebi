import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { qualifiedScreenName } from "../../lib/screeningPrompt";
import type { Repo, Task } from "../../types";
import { ControlShelf } from "../brew/ControlShelf";
import { EventStream } from "./EventStream";
import type { JobKind } from "./jobStyle";
import "./ops.css";
import { OpsStats } from "./OpsStats";
import { RunnersRail } from "./RunnersRail";
import { ToolbeltRail } from "./ToolbeltRail";
import { useDeckMood } from "./useDeckMood";
import { useMissionData } from "./useMissionData";
import { WorkerGrid } from "./WorkerGrid";

const SEV_COLOR: Record<string, string> = {
  critical: "text-red-400 font-bold",
  high: "text-red-400",
  medium: "text-amber-400",
  low: "text-ink-400",
  info: "text-sky-400",
};

export function OpsDeck({
  tasks,
  repos,
  onNewTask,
  onCancel,
}: {
  tasks: Task[];
  repos: Repo[];
  onNewTask: (kind?: JobKind) => void;
  onCancel?: (taskId: number) => void;
}) {
  const navigate = useNavigate();
  const [director, setDirector] = useState(false);
  const [highlightedSlot, setHighlightedSlot] = useState<number | null>(null);
  const { mood, flash } = useDeckMood(tasks);

  const {
    now,
    backends,
    concurrency,
    screens,
    findings,
    workerSlots,
    slots,
    modules,
    runners,
    shipped,
    faulted,
    needsYou,
    orderCounts,
    pending,
  } = useMissionData({ tasks, repos });

  const runningCount = workerSlots.filter((s) => s.task.status === "running").length;

  const clock = new Date(now).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });

  return (
    <div
      className="ops-deck relative overflow-hidden space-y-3 font-mono animate-fade-up"
      data-mood={mood}
      data-flash={flash}
    >
      {/* Ambient scanline overlay */}
      <div
        aria-hidden="true"
        className="ops-scanline pointer-events-none absolute top-0 left-0 right-0 h-32 opacity-40 select-none"
      />

      {/* Circuit pulse: travelling light along the top seam */}
      <div
        aria-hidden="true"
        className="ops-deck-pulse-h pointer-events-none absolute select-none"
      />

      {/* 1. Header Bar */}
      <div className="surface flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2 text-xs">
        {/* Ops Logo / Glyphs */}
        <div className="flex items-center gap-2">
          <span className="flex h-5 w-5 items-center justify-center rounded bg-sky-500/20 text-sky-400 font-bold text-xs border border-sky-500/30">
            &gt;_
          </span>
          <h2 className="panel-title text-sm tracking-wide text-ink-100 uppercase">Ops Deck</h2>
        </div>

        {/* Subtitle / Status telemetry */}
        <p className="text-ink-500 text-[11px]">
          {concurrency === 0
            ? "pipeline suspended · 0 cores active"
            : workerSlots.length === 0
              ? "all worker cores idle · ready for dispatch"
              : `${runningCount} job${runningCount === 1 ? "" : "s"} running`}
          {needsYou > 0 && (
            <span className="text-amber-300">
              {" · "}
              {needsYou} need{needsYou === 1 ? "" : "s"} you
            </span>
          )}
          {" · "}
          terminal console
        </p>

        {/* Right side controls: Director Toggle, Clock */}
        <div className="ml-auto flex flex-wrap items-center gap-3">
          {/* Director Mode Toggle */}
          <button
            type="button"
            onClick={() => setDirector((d) => !d)}
            aria-pressed={director}
            title="Director mode spotlights the most recently active process core"
            className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[11px] font-medium transition-all duration-200 cursor-pointer border ${
              director
                ? "border-sky-500/60 bg-sky-500/20 text-sky-300 shadow-[0_0_10px_rgba(56,189,248,0.25)]"
                : "border-ink-800 bg-ink-950/40 text-ink-400 hover:border-ink-700 hover:text-ink-200"
            }`}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                director ? "bg-sky-400 ops-led" : "bg-ink-600"
              }`}
            />
            <span>Director</span>
            <span
              className={`text-[9px] uppercase px-1 rounded ${
                director ? "bg-sky-400/20 text-sky-200" : "bg-ink-850 text-ink-500"
              }`}
            >
              {director ? "ON" : "OFF"}
            </span>
          </button>

          {/* Clock */}
          <span className="font-mono text-xs tabular-nums text-ink-400 border-l border-ink-800 pl-3">
            {clock}
          </span>
        </div>
      </div>

      {/* 2. Panoramic Security & Health Radar Marquee */}
      <div
        className="surface ops-radar-marquee flex items-center gap-3 overflow-hidden px-3 py-1.5 text-xs"
        role="group"
        aria-label="Security & Audit radar"
      >
        <div className="flex shrink-0 items-center gap-2 border-r border-ink-800 pr-3">
          <span
            className={`h-2 w-2 rounded-full ${
              screens.some((s) => s.latest_run?.status === "running")
                ? "bg-amber-400 animate-ping"
                : "bg-sky-400 ops-led"
            }`}
          />
          <Link
            to="/screenings"
            state={{ from: "mission" }}
            className="font-semibold uppercase tracking-wider text-sky-400 hover:text-sky-300 transition-colors"
          >
            Audit radar
          </Link>
          <span className="text-[10px] text-ink-500">
            {screens.length} screen{screens.length === 1 ? "" : "s"}
          </span>
        </div>

        {findings.length > 0 ? (
          <div className="min-w-0 flex-1 overflow-hidden">
            <div
              className="ops-radar-track flex w-max gap-8"
              style={{
                animationDuration: `${Math.max(90, Math.min(8, findings.length) * 22)}s`,
              }}
            >
              {[...findings.slice(0, 8), ...findings.slice(0, 8)].map((f, i) =>
                i < Math.min(8, findings.length) ? (
                  <Link
                    key={`${f.screen_id}-${f.title}-${i}`}
                    to="/screenings"
                    state={{ from: "mission" }}
                    className="flex items-center gap-1.5 whitespace-nowrap text-[11px] text-ink-300 transition-colors hover:text-sky-300"
                    title={`${qualifiedScreenName(f.screen_name, f.repo_full_name)}: ${f.title}`}
                  >
                    <span className={SEV_COLOR[f.severity] ?? "text-ink-400"}>
                      [{f.severity.toUpperCase()}]
                    </span>
                    <span className="text-ink-200">{f.title}</span>
                    <span className="text-ink-500">
                      ({qualifiedScreenName(f.screen_name, f.repo_full_name)})
                    </span>
                  </Link>
                ) : (
                  <span
                    key={`${f.screen_id}-${f.title}-${i}`}
                    aria-hidden="true"
                    className="flex items-center gap-1.5 whitespace-nowrap text-[11px] text-ink-300"
                  >
                    <span className={SEV_COLOR[f.severity] ?? "text-ink-400"}>
                      [{f.severity.toUpperCase()}]
                    </span>
                    <span className="text-ink-200">{f.title}</span>
                    <span className="text-ink-500">
                      ({qualifiedScreenName(f.screen_name, f.repo_full_name)})
                    </span>
                  </span>
                )
              )}
            </div>
          </div>
        ) : (
          <div className="flex-1 truncate text-[11px] text-ink-500">
            All quiet — zero security or stability findings detected across connected repositories.
          </div>
        )}
      </div>

      {/* 3. Three-Column Single-Viewport Bounded Grid */}
      <div className="grid gap-3 xl:grid-cols-[230px_minmax(0,1fr)_270px] xl:overflow-hidden">
        {/* Left Column: Runners + Toolbelt rails */}
        <div className="flex min-h-0 min-w-0 flex-col gap-3 xl:h-[calc(100vh-290px)] xl:min-h-[480px]">
          <div className="flex min-h-0 min-w-0 flex-1 flex-col [&>section]:flex-1">
            <RunnersRail
              runners={runners}
              hoveredCore={highlightedSlot}
              onHoverCore={setHighlightedSlot}
            />
          </div>
          <div className="flex min-h-0 min-w-0 flex-1 flex-col [&>section]:flex-1">
            <ToolbeltRail
              modules={modules}
              hoveredCore={highlightedSlot}
              onHoverCore={setHighlightedSlot}
            />
          </div>
        </div>

        {/* Center Column: Worker Process Grid */}
        <div className="flex min-h-0 min-w-0 flex-col xl:h-[calc(100vh-290px)] xl:min-h-[480px] [&>section]:flex-1">
          <WorkerGrid
            workerSlots={workerSlots}
            slots={slots}
            pending={pending}
            shipped={shipped}
            faulted={faulted}
            orderCounts={orderCounts}
            now={now}
            highlightedSlot={highlightedSlot}
            director={director}
            onOpen={(id) => navigate(`/tasks/${id}`, { state: { from: "mission" } })}
            onOrder={(kind) => onNewTask(kind)}
            onNewTaskKind={(kind) => onNewTask(kind)}
            onCancel={onCancel}
          />
        </div>

        {/* Right Column: Stats + ControlShelf + EventStream */}
        <div className="flex min-h-0 min-w-0 flex-col gap-2.5 xl:grid xl:h-[calc(100vh-290px)] xl:min-h-[480px] xl:grid-cols-[minmax(0,1fr)] xl:grid-rows-[auto_minmax(0,1.4fr)_minmax(0,0.7fr)] xl:overflow-hidden">
          <OpsStats tasks={tasks} now={now} />

          <ControlShelf
            tasks={tasks}
            repos={repos}
            screens={screens}
            findings={findings}
            backends={backends}
            compact
          />

          <EventStream
            tasks={tasks}
            screens={screens}
            findings={findings}
            now={now}
            repos={repos}
          />
        </div>
      </div>
    </div>
  );
}

export default OpsDeck;
