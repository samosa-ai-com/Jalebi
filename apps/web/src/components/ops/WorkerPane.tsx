import { useEffect, useMemo, useRef, useState } from "react";
import { taskEvents } from "../../api/client";
import { buildActivityBars } from "../../lib/runningCard";
import type { Step } from "../../types";
import { Sparkline } from "../RunningCard";
import {
  JOB_ACCENT,
  JOB_LABEL,
  JobGlyph,
  jobForType,
  type JobKind,
} from "./jobStyle";
import type { WorkerSlot } from "./useMissionData";

export interface WorkerPaneProps {
  core: number;
  job: WorkerSlot | null;
  now: number;
  compact?: boolean;
  highlighted?: boolean;
  director?: boolean;
  onOpen: (id: number) => void;
  onOrder: (kind?: JobKind) => void;
  onCancel?: (taskId: number) => void;
}

interface LogLine {
  id: string;
  time: string;
  text: string;
  seq?: number;
}

function normalizeText(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

function formatTime(isoOrMs?: string | number | null): string {
  const d = isoOrMs ? new Date(isoOrMs) : new Date();
  if (Number.isNaN(d.getTime())) return new Date().toLocaleTimeString([], { hour12: false });
  return d.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function formatElapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  if (h > 0) return `${h}h ${m % 60}m`;
  if (m > 0) return `${m}m ${s % 60}s`;
  return `${s}s`;
}

export function WorkerPane({
  core,
  job,
  now,
  compact = false,
  highlighted = false,
  director = false,
  onOpen,
  onOrder,
  onCancel,
}: WorkerPaneProps) {
  const logScrollRef = useRef<HTMLDivElement>(null);
  const [isHovered, setIsHovered] = useState(false);
  const [streamedMap, setStreamedMap] = useState<{ key: string; lines: LogLine[] }>({
    key: "",
    lines: [],
  });

  const isQueued = job?.task.status === "queued";
  const isRunning = job?.task.status === "running";
  const needsYou = job?.task.attention === "needs_you";

  const taskId = job?.task.id;
  const runId = job?.task.run?.id;
  const currentKey = `${taskId ?? 0}-${runId ?? 0}`;

  const rawSteps = useMemo<Step[]>(
    () => (job?.task.run?.steps ?? []) as Step[],
    [job?.task.run?.steps]
  );

  const initialLines = useMemo<LogLine[]>(() => {
    const list: LogLine[] = [];
    for (let i = 0; i < rawSteps.length; i += 1) {
      const s = rawSteps[i];
      if (s.text && s.text.trim()) {
        list.push({
          id: `step-${i}-${s.ts}`,
          time: formatTime(s.ts),
          text: normalizeText(s.text),
          seq: s.seq,
        });
      }
    }
    return list.slice(-120);
  }, [rawSteps]);

  const rawStepSeqs = useMemo(() => {
    const set = new Set<number>();
    for (const s of rawSteps) {
      if (typeof s.seq === "number") set.add(s.seq);
    }
    return set;
  }, [rawSteps]);

  const rawStepSeqsRef = useRef(rawStepSeqs);
  useEffect(() => {
    rawStepSeqsRef.current = rawStepSeqs;
  }, [rawStepSeqs]);

  // SSE event subscriber while running: appends new streamed lines
  useEffect(() => {
    if (!isRunning || !taskId || !runId) return;

    const sseSeqs = new Set<number>();

    const stop = taskEvents(
      taskId,
      (ev) => {
        if (ev.text) {
          const clean = normalizeText(ev.text);
          if (clean) {
            if (typeof ev.seq === "number") {
              if (rawStepSeqsRef.current.has(ev.seq) || sseSeqs.has(ev.seq)) {
                return;
              }
              sseSeqs.add(ev.seq);
            }

            const newLine: LogLine = {
              id: `live-${typeof ev.seq === "number" ? ev.seq : Date.now()}-${Math.random()}`,
              time: formatTime(),
              text: clean,
              seq: ev.seq,
            };
            setStreamedMap((prev) => {
              const prevLines = prev.key === currentKey ? prev.lines : [];
              // If no seq, skip if normalized text equals immediately preceding line
              if (typeof ev.seq !== "number" && prevLines.length > 0) {
                const prevLast = prevLines[prevLines.length - 1];
                if (normalizeText(prevLast.text) === clean) {
                  return prev;
                }
              }
              const updated = [...prevLines, newLine];
              return {
                key: currentKey,
                lines: updated.length > 120 ? updated.slice(updated.length - 120) : updated,
              };
            });
          }
        }
      },
      () => {}
    );

    return () => stop();
  }, [taskId, isRunning, runId, currentKey]);

  const liveLines = useMemo(() => {
    const extra = streamedMap.key === currentKey ? streamedMap.lines : [];
    const seenSeqs = new Set(rawStepSeqs);
    const validExtra: LogLine[] = [];
    for (const line of extra) {
      if (typeof line.seq === "number") {
        if (seenSeqs.has(line.seq)) continue;
        seenSeqs.add(line.seq);
      }
      validExtra.push(line);
    }

    const merged = [...initialLines, ...validExtra];

    const deduped: LogLine[] = [];
    for (const line of merged) {
      if (typeof line.seq !== "number" && deduped.length > 0) {
        const prevText = normalizeText(deduped[deduped.length - 1].text);
        if (normalizeText(line.text) === prevText) {
          continue;
        }
      }
      deduped.push(line);
    }

    return deduped.slice(-120);
  }, [initialLines, streamedMap, currentKey, rawStepSeqs]);

  // Auto-scroll log body to bottom on new lines unless user is hovering/interacting
  useEffect(() => {
    if (!isHovered && logScrollRef.current) {
      logScrollRef.current.scrollTop = logScrollRef.current.scrollHeight;
    }
  }, [liveLines, isHovered]);

  const elapsedMs = job?.startedAtMs ? Math.max(0, now - job.startedAtMs) : 0;
  const progress = job
    ? job.timeoutMs && job.timeoutMs > 0
      ? Math.min(1, elapsedMs / job.timeoutMs)
      : Math.min(1, 0.15 + job.steps * 0.05)
    : 0;

  const kind = job ? jobForType(job.task.type) : "feature";
  const accent = JOB_ACCENT[kind];

  const bars = useMemo(() => buildActivityBars(rawSteps), [rawSteps]);
  const toolCallCount = useMemo(
    () => rawSteps.filter((s) => s.type === "tool_call").length,
    [rawSteps]
  );
  const prNumber = job?.task.prs?.length ? job.task.prs[0] : (job?.task.pr_number ?? null);
  const branch = job?.task.target_branch ?? job?.task.source_branch ?? null;

  // Elevation state: spotlight when director is active and highlighted
  const isSpotlighted = director && highlighted;

  // -------------------------------------------------------------
  // 1. IDLE STATE: Clean terminal prompt, ready for dispatch
  // -------------------------------------------------------------
  if (!job) {
    return (
      <div
        className={`ops-pane-idle relative flex flex-col justify-between rounded-xl border p-3 font-mono transition-all duration-300 ${
          compact ? "min-h-[190px]" : "min-h-[240px]"
        } ${highlighted ? "ring-2 ring-sky-500/50 border-sky-500/60" : ""}`}
      >
        {/* Terminal Header */}
        <div className="flex items-center justify-between border-b border-ink-850 pb-2">
          <div className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-ink-600" />
            <span className="h-2 w-2 rounded-full bg-ink-700" />
            <span className="h-2 w-2 rounded-full bg-ink-800" />
            <span className="ml-1 text-[11px] font-semibold tracking-wider text-ink-400">
              CORE-0{core}
            </span>
          </div>
          <span className="flex items-center gap-1.5 text-[10px] text-ink-500">
            <span className="h-1.5 w-1.5 rounded-full bg-ink-600" />
            STANDBY
          </span>
        </div>

        {/* Console Prompt Body */}
        <div className="my-auto py-3">
          <div className="text-xs text-ink-400">
            <span className="text-sky-400">ops@deck</span>:<span className="text-ink-500">~/core-0{core}</span>$ idle
          </div>
          <p className="mt-1 text-[11px] text-ink-500">
            Worker core online. Awaiting task dispatch...
            <span className="ml-1 inline-block h-3.5 w-1.5 bg-sky-400 align-middle ops-caret" />
          </p>
        </div>

        {/* Spin up button */}
        <div className="pt-2 border-t border-ink-850/60 flex items-center justify-between">
          <span className="text-[10px] text-ink-600">0 tasks in pipeline</span>
          <button
            type="button"
            onClick={() => onOrder()}
            aria-label={`Spin up a job on core ${core}`}
            className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-ink-700 bg-ink-950/60 px-3 py-1 text-xs text-sky-300 transition-colors hover:border-sky-500/60 hover:bg-sky-500/10 hover:text-sky-200"
          >
            <span className="text-sky-400 font-bold">+</span>
            <span>spin up a job</span>
          </button>
        </div>
      </div>
    );
  }

  // -------------------------------------------------------------
  // 2. QUEUED STATE: Shimmering indeterminate bar, pending dispatch
  // -------------------------------------------------------------
  if (isQueued) {
    return (
      <div
        className={`ops-pane-queued relative flex flex-col justify-between rounded-xl border p-3 font-mono transition-all duration-300 ${
          compact ? "min-h-[190px]" : "min-h-[240px]"
        } ${highlighted ? "ring-2 ring-amber-500/50 border-amber-500/60" : ""}`}
      >
        {/* Terminal Header */}
        <div className="flex items-center justify-between border-b border-ink-850 pb-2">
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-semibold text-ink-400">CORE-0{core}</span>
            <span className="text-xs font-bold text-amber-400">#{job.task.id}</span>
            <span className="flex items-center gap-1 text-[10px] text-ink-300">
              <JobGlyph kind={kind} className="h-3 w-3 text-amber-400" />
              {JOB_LABEL[kind]}
            </span>
          </div>
          <span className="rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-semibold text-amber-300 border border-amber-500/20">
            QUEUED
          </span>
        </div>

        {/* Queued Body */}
        <div className="my-auto py-2">
          <div className="relative h-1.5 w-full overflow-hidden rounded bg-ink-850">
            <div className="absolute inset-0 bg-gradient-to-r from-transparent via-amber-400/50 to-transparent ops-shimmer" />
          </div>
          <p className="mt-2 line-clamp-2 text-xs text-ink-200" title={job.task.prompt}>
            {job.task.prompt}
          </p>
          <div className="mt-2 flex items-center gap-2 text-[10px] text-ink-500">
            {job.repoName && <span>repo: {job.repoName.split("/")[1] ?? job.repoName}</span>}
            {job.task.blocked && (
              <span className="rounded bg-amber-950/80 px-1 text-amber-400 border border-amber-700/50">
                blocked
              </span>
            )}
          </div>
        </div>

        {/* Queued Footer */}
        <div className="flex items-center justify-between border-t border-ink-850/60 pt-2">
          <button
            type="button"
            onClick={() => onOpen(job.task.id)}
            className="cursor-pointer text-xs text-sky-400 hover:text-sky-300 hover:underline"
          >
            inspect ticket →
          </button>
          {onCancel && (
            <button
              type="button"
              onClick={() => onCancel(job.task.id)}
              className="cursor-pointer rounded px-2 py-0.5 text-xs text-ink-500 transition-colors hover:bg-red-500/10 hover:text-red-400"
            >
              cancel
            </button>
          )}
        </div>
      </div>
    );
  }

  // -------------------------------------------------------------
  // 3. RUNNING / NEEDS-YOU STATE: Live terminal log + telemetry
  // -------------------------------------------------------------
  return (
    <div
      className={`relative flex flex-col justify-between rounded-xl border p-3 font-mono transition-all duration-300 ${
        compact ? "min-h-[220px]" : "min-h-[280px]"
      } ${needsYou ? "ops-pane-needs-you" : "ops-pane-running"} ${
        isSpotlighted ? "ops-spot ring-2 ring-sky-400" : ""
      }`}
    >
      {/* Pane Header */}
      <div className="flex items-center justify-between border-b border-ink-850 pb-2 text-xs">
        <div className="flex items-center gap-2 min-w-0">
          <div className="flex items-center gap-1 shrink-0">
            <span
              className={`h-2 w-2 rounded-full ${
                needsYou
                  ? "bg-amber-400 animate-ping"
                  : isRunning
                    ? "bg-emerald-400 ops-led"
                    : "bg-ink-500"
              }`}
            />
            <span className="text-[11px] font-semibold text-ink-400">CORE-0{core}</span>
          </div>

          <span className="font-bold text-sky-300 shrink-0">#{job.task.id}</span>

          <span className={`inline-flex items-center gap-1 rounded px-1.5 py-px text-[10px] ${accent.badge}`}>
            <JobGlyph kind={kind} className="h-3 w-3" />
            <span>{JOB_LABEL[kind]}</span>
          </span>

          {job.avatarUrl && (
            <img src={job.avatarUrl} alt="" className="h-4 w-4 shrink-0 rounded-full" />
          )}
          <span className="truncate text-[11px] text-ink-300" title={job.agentName ?? "agent"}>
            {job.agentName ?? job.task.cli ?? "agent"}
          </span>
        </div>

        <div className="flex items-center gap-2 shrink-0 text-[11px] tabular-nums text-ink-400">
          <span>{formatElapsed(elapsedMs)}</span>
          {job.steps > 0 && <span className="text-ink-500">· {job.steps} steps</span>}
        </div>
      </div>

      {/* Repo / Branch & Prompt Subheader */}
      <div className="mt-1 flex items-baseline justify-between gap-2 text-[10px] text-ink-500">
        <span className="truncate" title={job.task.prompt}>
          <span className="text-ink-300 font-medium">{job.repoName?.split("/")[1] ?? "repo"}</span>
          {branch && <span className="text-ink-500">:{branch}</span>}
          {" — "}
          <span className="text-ink-400">{job.task.prompt}</span>
        </span>
        {needsYou && (
          <span className="shrink-0 rounded bg-amber-500/20 px-1.5 py-px text-[10px] font-semibold text-amber-300 animate-pulse border border-amber-500/40">
            awaiting input
          </span>
        )}
      </div>

      {/* Live Log Stream Body */}
      <div
        ref={logScrollRef}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
        className="my-2 h-24 overflow-y-auto rounded-lg border border-ink-850/80 bg-ink-950/80 p-2 text-[11px] leading-relaxed shadow-inner"
      >
        {liveLines.length === 0 ? (
          <div className="flex h-full items-center justify-center text-ink-600 text-[10px]">
            initializing process telemetry...
          </div>
        ) : (
          liveLines.map((line) => (
            <div key={line.id} className="ops-line-in flex items-start gap-1.5 py-0.5">
              <span className="shrink-0 text-[10px] tabular-nums text-ink-600">{line.time}</span>
              <span className="text-ink-500 shrink-0">›</span>
              <span className="break-all text-ink-200">{line.text}</span>
            </div>
          ))
        )}
      </div>

      {/* Progress Striped Bar */}
      <div className="w-full">
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-ink-850">
          <div
            className={`h-full transition-all duration-500 ops-stripes ${
              needsYou ? "bg-amber-500" : "bg-sky-500"
            }`}
            style={{ width: `${Math.max(5, Math.min(100, Math.round(progress * 100)))}%` }}
          />
        </div>
      </div>

      {/* Footer: Sparkline, PR link, Tool calls, Buttons */}
      <div className="mt-2 flex flex-wrap items-center justify-between gap-1 border-t border-ink-850/60 pt-1.5 text-xs">
        <div className="flex items-center gap-2">
          {bars.length > 0 && <Sparkline bars={bars} />}
          {toolCallCount > 0 && (
            <span className="text-[10px] text-ink-500">
              {toolCallCount} tool{toolCallCount === 1 ? "" : "s"}
            </span>
          )}
          {prNumber != null && job.repoName && (
            <a
              href={`https://github.com/${job.repoName}/pull/${prNumber}`}
              target="_blank"
              rel="noreferrer"
              onClick={(e) => e.stopPropagation()}
              className="text-[10px] text-sky-400 hover:text-sky-300 hover:underline"
              title={`Open PR #${prNumber}`}
            >
              PR #{prNumber} ↗
            </a>
          )}
        </div>

        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => onOpen(job.task.id)}
            aria-label={needsYou ? `Respond to task ${job.task.id}` : `Open task ${job.task.id}`}
            className={`cursor-pointer rounded-lg px-2.5 py-0.5 text-xs font-medium transition-colors ${
              needsYou
                ? "bg-amber-500 text-ink-950 hover:bg-amber-400 font-bold"
                : "bg-sky-500/20 text-sky-300 hover:bg-sky-500/30 border border-sky-500/30"
            }`}
          >
            {needsYou ? "respond →" : "open →"}
          </button>
          {onCancel && (
            <button
              type="button"
              onClick={() => onCancel(job.task.id)}
              aria-label={`Cancel task ${job.task.id}`}
              className="cursor-pointer rounded-lg border border-ink-800 px-2 py-0.5 text-xs text-ink-500 transition-colors hover:border-red-500/40 hover:bg-red-500/10 hover:text-red-400"
            >
              cancel
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default WorkerPane;
