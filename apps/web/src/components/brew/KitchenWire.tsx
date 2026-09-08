/**
 * Halwai Shop — Kitchen Wire.
 *
 * Real-time operational comms wire / terminal log: displays rolling chronological
 * events from agents, task lifecycle transitions, tool calls, and screening audits.
 * Gives the operator an active stream of shop intelligence.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { Screen, ScreeningFinding, Task } from "../../types";
import { lastMessageText } from "../../lib/runningCard";
import { snackForType } from "./snacks";

export interface WireEvent {
  id: string;
  time: string;
  tag: string;
  tagColor: string;
  text: string;
  link?: string;
}

function formatTime(isoOrMs: string | number | null | undefined): string {
  if (!isoOrMs) return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const d = new Date(isoOrMs);
  if (Number.isNaN(d.getTime())) return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function KitchenWire({
  tasks,
  screens,
  findings,
  now,
}: {
  tasks: Task[];
  screens: Screen[];
  findings: ScreeningFinding[];
  now: number;
}) {
  const [filter, setFilter] = useState<"all" | "agents" | "ops">("all");
  const scrollRef = useRef<HTMLDivElement>(null);
  const [isPaused, setIsPaused] = useState(false);

  // Derive chronological events from tasks, runs, and screenings
  const events = useMemo<WireEvent[]>(() => {
    const list: (WireEvent & { epoch: number })[] = [];

    for (const t of tasks) {
      const createdEpoch = new Date(t.created_at).getTime() || now;
      const updatedEpoch = new Date(t.updated_at).getTime() || createdEpoch;
      const snack = snackForType(t.type);

      // Active / Running tasks: show step telemetry
      if (t.status === "running" && t.run) {
        const startedEpoch = t.run.started_at ? new Date(t.run.started_at).getTime() : createdEpoch;
        const lastMsg = lastMessageText(t.run.steps ?? []);
        if (lastMsg) {
          list.push({
            id: `task-${t.id}-step`,
            epoch: updatedEpoch,
            time: formatTime(updatedEpoch),
            tag: `#${t.id} ${t.agent_id ?? "agent"}`,
            tagColor: "text-syrup-400",
            text: lastMsg,
            link: `/tasks/${t.id}`,
          });
        }
        list.push({
          id: `task-${t.id}-run`,
          epoch: startedEpoch,
          time: formatTime(startedEpoch),
          tag: `#${t.id} ${t.agent_id ?? "cook"}`,
          tagColor: "text-syrup-300",
          text: `Frying ${snack}: "${t.prompt.length > 50 ? t.prompt.slice(0, 50) + "…" : t.prompt}"`,
          link: `/tasks/${t.id}`,
        });
      }

      // Needs you attention
      if (t.attention === "needs_you") {
        list.push({
          id: `task-${t.id}-attn`,
          epoch: updatedEpoch,
          time: formatTime(updatedEpoch),
          tag: `#${t.id} ATTN`,
          tagColor: "text-amber-300 animate-pulse",
          text: `Cook needs confirmation: ${t.prompt.length > 40 ? t.prompt.slice(0, 40) + "…" : t.prompt}`,
          link: `/tasks/${t.id}`,
        });
      }

      // Queued
      if (t.status === "queued") {
        list.push({
          id: `task-${t.id}-queue`,
          epoch: createdEpoch,
          time: formatTime(createdEpoch),
          tag: `#${t.id} QUEUE`,
          tagColor: "text-ink-400",
          text: `Order ticket received: ${snack} (${t.repo_full_name ? t.repo_full_name.split("/")[1] ?? t.repo_full_name : "repo"})`,
          link: `/tasks/${t.id}`,
        });
      }

      // Done / Served
      if (t.status === "done") {
        list.push({
          id: `task-${t.id}-done`,
          epoch: updatedEpoch,
          time: formatTime(updatedEpoch),
          tag: `#${t.id} SERVED`,
          tagColor: "text-green-400",
          text: `Golden ${snack} served${t.pr_number ? ` · PR #${t.pr_number} open` : ""}`,
          link: `/tasks/${t.id}`,
        });
      }

      // Spoiled / Failed
      if (t.status === "failed" || t.status === "timed_out" || t.status === "interrupted") {
        list.push({
          id: `task-${t.id}-failed`,
          epoch: updatedEpoch,
          time: formatTime(updatedEpoch),
          tag: `#${t.id} SPOILED`,
          tagColor: "text-red-400",
          text: `Dish spoiled: ${t.status}`,
          link: `/tasks/${t.id}`,
        });
      }
    }

    // Screens / Findings events
    for (const f of findings.slice(0, 4)) {
      list.push({
        id: `finding-${f.screen_id}-${f.title}`,
        epoch: f.finished_at ? new Date(f.finished_at).getTime() : now - 30_000,
        time: formatTime(f.finished_at ?? now),
        tag: `[${f.severity.toUpperCase()}]`,
        tagColor: f.severity === "critical" || f.severity === "high" ? "text-red-400" : "text-amber-300",
        text: `${f.screen_name}: ${f.title}`,
        link: "/screenings",
      });
    }

    for (const s of screens.filter((sc) => sc.latest_run?.status === "running")) {
      list.push({
        id: `screen-run-${s.id}`,
        epoch: now,
        time: formatTime(now),
        tag: "AUDIT RADAR",
        tagColor: "text-sky-400 animate-pulse",
        text: `Active audit running: ${s.name}`,
        link: "/screenings",
      });
    }

    // Sort newest first
    list.sort((a, b) => b.epoch - a.epoch);
    return list.slice(0, 25);
  }, [tasks, screens, findings, now]);

  const filtered = useMemo(() => {
    if (filter === "agents") {
      return events.filter((e) => e.tag.includes("agent") || e.tag.includes("cook") || e.tag.includes("#"));
    }
    if (filter === "ops") {
      return events.filter((e) => e.tag.includes("AUDIT") || e.tag.includes("QUEUE") || e.tag.includes("SERVED"));
    }
    return events;
  }, [events, filter]);

  // Auto-scroll when new items arrive if not hovered
  useEffect(() => {
    if (!isPaused && scrollRef.current) {
      scrollRef.current.scrollTop = 0;
    }
  }, [filtered, isPaused]);

  return (
    <section
      className="surface flex min-h-0 flex-1 flex-col px-3 py-2.5"
      aria-label="Kitchen wire"
      onMouseEnter={() => setIsPaused(true)}
      onMouseLeave={() => setIsPaused(false)}
    >
      <div className="flex items-center justify-between border-b border-ink-800/60 pb-1.5">
        <div className="flex items-center gap-1.5">
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-syrup-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-syrup-500" />
          </span>
          <h3 className="panel-title text-xs tracking-wider">Kitchen wire</h3>
        </div>

        <div className="flex items-center gap-1 text-[10px] font-mono">
          <button
            type="button"
            onClick={() => setFilter("all")}
            className={`rounded px-1.5 py-0.5 transition-colors ${
              filter === "all" ? "bg-syrup-500/20 text-syrup-300" : "text-ink-500 hover:text-ink-300"
            }`}
          >
            all
          </button>
          <button
            type="button"
            onClick={() => setFilter("agents")}
            className={`rounded px-1.5 py-0.5 transition-colors ${
              filter === "agents" ? "bg-syrup-500/20 text-syrup-300" : "text-ink-500 hover:text-ink-300"
            }`}
          >
            agents
          </button>
          <button
            type="button"
            onClick={() => setFilter("ops")}
            className={`rounded px-1.5 py-0.5 transition-colors ${
              filter === "ops" ? "bg-syrup-500/20 text-syrup-300" : "text-ink-500 hover:text-ink-300"
            }`}
          >
            ops
          </button>
        </div>
      </div>

      <div
        ref={scrollRef}
        className="mt-2 min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-0.5 font-mono text-[11px]"
      >
        {filtered.length === 0 ? (
          <p className="mt-4 text-center text-xs text-ink-600">
            Frequencies quiet — standing by for orders.
          </p>
        ) : (
          filtered.map((ev) => (
            <div
              key={ev.id}
              className="group flex items-baseline gap-1.5 rounded p-1 transition-colors hover:bg-ink-850/50"
            >
              <span className="shrink-0 text-[9px] tabular-nums text-ink-600">{ev.time}</span>
              <span className={`shrink-0 text-[10px] font-medium ${ev.tagColor}`}>{ev.tag}</span>
              {ev.link ? (
                <Link
                  to={ev.link}
                  className="min-w-0 flex-1 truncate text-ink-300 hover:text-syrup-300 hover:underline"
                  title={ev.text}
                >
                  {ev.text}
                </Link>
              ) : (
                <span className="min-w-0 flex-1 truncate text-ink-400" title={ev.text}>
                  {ev.text}
                </span>
              )}
            </div>
          ))
        )}
      </div>
    </section>
  );
}

export default KitchenWire;
