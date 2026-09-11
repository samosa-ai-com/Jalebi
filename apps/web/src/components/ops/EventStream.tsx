import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { lastMessageText } from "../../lib/runningCard";
import { qualifiedScreenName } from "../../lib/screeningPrompt";
import type { Repo, Screen, ScreeningFinding, Task } from "../../types";
import { JOB_LABEL, jobForType } from "./jobStyle";

export type EventCategory = "all" | "live" | "alerts" | "shipped" | "faulted";

export interface StreamEvent {
  id: string;
  epoch: number;
  time: string;
  tag: string;
  tagColor: string;
  text: string;
  link?: string;
  category: "live" | "alerts" | "shipped" | "faulted";
}

const FAULT_LABELS: Record<string, string> = {
  failed: "Process failed",
  timed_out: "Process timed out",
  interrupted: "Process interrupted",
  cancelled: "Process cancelled",
};

function formatTime(isoOrMs: string | number | null | undefined): string {
  if (!isoOrMs) {
    return new Date().toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  }
  const d = new Date(isoOrMs);
  if (Number.isNaN(d.getTime())) {
    return new Date().toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  }
  return d.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export function EventStream({
  tasks,
  screens,
  findings,
  now,
  repos = [],
}: {
  tasks: Task[];
  screens: Screen[];
  findings: ScreeningFinding[];
  now: number;
  repos?: Repo[];
}) {
  const [filter, setFilter] = useState<EventCategory>("all");
  const scrollRef = useRef<HTMLDivElement>(null);
  const [isPaused, setIsPaused] = useState(false);

  const events = useMemo<StreamEvent[]>(() => {
    const list: StreamEvent[] = [];

    for (const t of tasks) {
      const createdEpoch = new Date(t.created_at).getTime() || now;
      const updatedEpoch = new Date(t.updated_at).getTime() || createdEpoch;
      const kind = jobForType(t.type);
      const label = JOB_LABEL[kind];

      // Running tasks
      if (t.status === "running" && t.run) {
        const startedEpoch = t.run.started_at ? new Date(t.run.started_at).getTime() : createdEpoch;
        const lastMsg = lastMessageText(t.run.steps ?? []);
        if (lastMsg) {
          list.push({
            id: `task-${t.id}-step`,
            epoch: updatedEpoch,
            time: formatTime(updatedEpoch),
            tag: `RUN #${t.id}`,
            tagColor: "text-sky-400",
            text: lastMsg,
            link: `/tasks/${t.id}`,
            category: "live",
          });
        }
        list.push({
          id: `task-${t.id}-run`,
          epoch: startedEpoch,
          time: formatTime(startedEpoch),
          tag: `RUN #${t.id}`,
          tagColor: "text-sky-300",
          text: `Executing ${label}: "${t.prompt.length > 50 ? t.prompt.slice(0, 50) + "…" : t.prompt}"`,
          link: `/tasks/${t.id}`,
          category: "live",
        });
      }

      // Attention needed
      if (t.attention === "needs_you") {
        list.push({
          id: `task-${t.id}-attn`,
          epoch: updatedEpoch,
          time: formatTime(updatedEpoch),
          tag: `ATTN #${t.id}`,
          tagColor: "text-amber-300 animate-pulse",
          text: `Operator input required: ${t.prompt.length > 40 ? t.prompt.slice(0, 40) + "…" : t.prompt}`,
          link: `/tasks/${t.id}`,
          category: "alerts",
        });
      }

      // Queued
      if (t.status === "queued") {
        const repoName = t.repo_full_name
          ? t.repo_full_name.split("/")[1] ?? t.repo_full_name
          : "repo";
        list.push({
          id: `task-${t.id}-queue`,
          epoch: createdEpoch,
          time: formatTime(createdEpoch),
          tag: `QUEUE #${t.id}`,
          tagColor: "text-ink-400",
          text: `Job dispatched: ${label} (${repoName})`,
          link: `/tasks/${t.id}`,
          category: "live",
        });
      }

      // Done / Shipped
      if (t.status === "done") {
        list.push({
          id: `task-${t.id}-done`,
          epoch: updatedEpoch,
          time: formatTime(updatedEpoch),
          tag: `SHIPPED #${t.id}`,
          tagColor: "text-emerald-400",
          text: `Shipped ${label}${t.pr_number ? ` · PR #${t.pr_number}` : ""}`,
          link: `/tasks/${t.id}`,
          category: "shipped",
        });
      }

      // Failed / Faulted
      if (t.status === "failed" || t.status === "timed_out" || t.status === "interrupted") {
        list.push({
          id: `task-${t.id}-failed`,
          epoch: updatedEpoch,
          time: formatTime(updatedEpoch),
          tag: `FAULT #${t.id}`,
          tagColor: "text-red-400",
          text: FAULT_LABELS[t.status] ?? `Faulted (${t.status})`,
          link: `/tasks/${t.id}`,
          category: "faulted",
        });
      }
    }

    // Screening findings
    for (const f of findings.slice(0, 4)) {
      list.push({
        id: `finding-${f.screen_id}-${f.title}`,
        epoch: f.finished_at ? new Date(f.finished_at).getTime() : now - 30_000,
        time: formatTime(f.finished_at ?? now),
        tag: `[${f.severity.toUpperCase()}]`,
        tagColor:
          f.severity === "critical" || f.severity === "high" ? "text-red-400" : "text-amber-300",
        text: `${qualifiedScreenName(f.screen_name, f.repo_full_name)}: ${f.title}`,
        link: "/screenings",
        category: "alerts",
      });
    }

    // Active screen audits
    for (const s of screens.filter((sc) => sc.latest_run?.status === "running")) {
      list.push({
        id: `screen-run-${s.id}`,
        epoch: now,
        time: formatTime(now),
        tag: "AUDIT RADAR",
        tagColor: "text-sky-400 animate-pulse",
        text: `Active audit running: ${qualifiedScreenName(
          s.name,
          repos.find((r) => r.id === s.repo_id)?.full_name
        )}`,
        link: "/screenings",
        category: "alerts",
      });
    }

    list.sort((a, b) => b.epoch - a.epoch);
    return list.slice(0, 30);
  }, [tasks, screens, findings, now, repos]);

  const filtered = useMemo(() => {
    if (filter === "all") return events;
    return events.filter((e) => e.category === filter);
  }, [events, filter]);

  useEffect(() => {
    if (!isPaused && scrollRef.current) {
      scrollRef.current.scrollTop = 0;
    }
  }, [filtered, isPaused]);

  return (
    <section
      className="surface flex min-h-0 min-w-0 flex-1 flex-col px-3 py-2.5 font-mono"
      aria-label="Ops event stream"
      onMouseEnter={() => setIsPaused(true)}
      onMouseLeave={() => setIsPaused(false)}
    >
      <div className="flex flex-wrap items-center justify-between gap-1 border-b border-ink-850 pb-1.5">
        <div className="flex items-center gap-1.5">
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-sky-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-sky-500" />
          </span>
          <h3 className="panel-title text-xs uppercase tracking-wider text-ink-300">
            Event Stream
          </h3>
        </div>

        <div className="flex flex-wrap items-center gap-1 text-[10px]">
          {(["all", "live", "alerts", "shipped", "faulted"] as const).map((cat) => {
            const active = filter === cat;
            return (
              <button
                key={cat}
                type="button"
                onClick={() => setFilter(cat)}
                className={`inline-flex items-center min-h-6 rounded px-1.5 py-0.5 transition-colors cursor-pointer ${
                  active
                    ? cat === "alerts"
                      ? "bg-amber-500/25 text-amber-300 font-semibold"
                      : cat === "live"
                        ? "bg-sky-500/25 text-sky-300 font-semibold"
                        : cat === "shipped"
                          ? "bg-emerald-500/25 text-emerald-300 font-semibold"
                          : cat === "faulted"
                            ? "bg-red-500/25 text-red-300 font-semibold"
                            : "bg-sky-500/25 text-sky-300 font-semibold"
                    : "text-ink-500 hover:text-ink-300"
                }`}
              >
                {cat}
              </button>
            );
          })}
        </div>
      </div>

      <div
        ref={scrollRef}
        className="mt-2 min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-0.5 text-[11px]"
      >
        {filtered.length === 0 ? (
          <div className="flex min-h-full items-center justify-center px-2 text-center text-xs text-ink-500">
            No events logged in this category.
          </div>
        ) : (
          filtered.map((e) => (
            <div
              key={e.id}
              className="group flex items-baseline gap-2 rounded px-1.5 py-1 transition-colors hover:bg-ink-850/60"
            >
              <span className="shrink-0 text-[10px] tabular-nums text-ink-500">{e.time}</span>
              <span className={`shrink-0 text-[10px] font-semibold ${e.tagColor}`}>{e.tag}</span>
              {e.link ? (
                <Link
                  to={e.link}
                  state={{ from: "mission" }}
                  className="min-w-0 flex-1 truncate text-ink-300 transition-colors hover:text-sky-300"
                  title={e.text}
                >
                  {e.text}
                </Link>
              ) : (
                <span className="min-w-0 flex-1 truncate text-ink-400" title={e.text}>
                  {e.text}
                </span>
              )}
            </div>
          ))
        )}
      </div>
    </section>
  );
}

export default EventStream;
