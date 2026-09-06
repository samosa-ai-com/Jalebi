/**
 * Phase 4 T4.4 — live "running now" card.
 *
 * Compact per-task card rendered at the top of the Tasks page while
 * the task is in `queued` or `running`. Shows:
 *   - pulsing status dot (status pill)
 *   - live elapsed timer (client ticker, 1 s)
 *   - step count
 *   - last `message`-type step text
 *   - backend / CLI tag
 *   - PR link (first linked PR, when the repo name is known)
 *   - inline activity sparkline (40×16, 40-bucket tool-call density)
 *
 * No new dependencies; the existing 5 s `/api/tasks` poll feeds the panel.
 * Per-task SSE (`taskEvents`) keeps the step count + last-message fresh in
 * between polls. The Tasks page renders one card per queued/running task
 * inside a scroll-bounded panel.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { taskEvents } from "../api/client";
import { StatusBadge } from "./StatusBadge";
import {
  buildActivityBars,
  formatElapsed,
  lastMessageText,
} from "../lib/runningCard";
import type { Run, Task } from "../types";

function Sparkline({ bars }: { bars: number[] }) {
  if (bars.every((b) => b === 0)) return null;
  const max = Math.max(...bars, 1);
  const w = 40;
  const h = 16;
  const bw = w / bars.length;
  return (
    <svg
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      role="img"
      aria-label="tool-call activity"
      className="shrink-0"
    >
      {bars.map((c, i) => {
        const bh = (c / max) * h;
        const y = h - bh;
        return (
          <rect
            key={i}
            x={i * bw}
            y={y}
            width={Math.max(bw - 0.5, 0.5)}
            height={Math.max(bh, 0.5)}
            className="fill-syrup-400/70"
          />
        );
      })}
    </svg>
  );
}

export function RunningCard({ task, repoName }: { task: Task; repoName?: string }) {
  const run = task.run;
  const [, setTick] = useState(0);
  // Re-render every 1 s so the elapsed timer advances.
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, []);

  // Per-task SSE: re-fetches steps on every published event. The Tasks page
  // bounds the number of live EventSources (≤ concurrency=4); this card
  // is one consumer.
  const runId = run?.id;
  useEffect(() => {
    if (!runId) return;
    const stop = taskEvents(
      task.id,
      () => setTick((t) => t + 1),  // re-render on each new event
      () => setTick((t) => t + 1)
    );
    return stop;
  }, [task.id, runId]);

  const steps = (run?.steps ?? []) as Run["steps"];
  const lastMsg = lastMessageText(steps as never) ?? null;
  const bars = buildActivityBars(steps as never);
  const elapsed = formatElapsed(run?.started_at ?? null);
  const prNumber =
    task.prs?.length ? task.prs[0] : (task.pr_number ?? null);

  return (
    <div className="surface flex flex-wrap items-center gap-3 px-4 py-3">
      <Link
        to={`/tasks/${task.id}`}
        className="font-mono text-sm text-syrup-400 hover:text-syrup-300"
      >
        #{task.id}
      </Link>
      <StatusBadge status={task.status} />
      {run && (
        <span className="font-mono text-xs tabular-nums text-ink-300" title="elapsed">
          {elapsed}
        </span>
      )}
      {run && (
        <span className="font-mono text-xs text-ink-500">
          {steps.length} step{steps.length === 1 ? "" : "s"}
        </span>
      )}
      {lastMsg && (
        <span className="min-w-0 flex-1 truncate text-xs text-ink-400" title={lastMsg}>
          {lastMsg}
        </span>
      )}
      {task.cli && (
        <span className="rounded bg-ink-800/60 px-1.5 py-0.5 font-mono text-[10px] text-ink-400">
          {task.cli}
          {task.model ? `:${task.model}` : ""}
        </span>
      )}
      {repoName && prNumber != null && (
        <a
          className="font-mono text-xs text-syrup-400 hover:text-syrup-300"
          href={`https://github.com/${repoName}/pull/${prNumber}`}
          target="_blank"
          rel="noreferrer"
          title={`PR #${prNumber} on ${repoName}`}
        >
          #{prNumber}
        </a>
      )}
      {bars.length > 0 && <Sparkline bars={bars} />}
    </div>
  );
}

export default RunningCard;