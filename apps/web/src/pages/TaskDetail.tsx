import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, taskEvents } from "../api/client";
import { StatusBadge } from "../components/StatusBadge";
import type { Followup, Repo, SseEvent, Task } from "../types";

const TERMINAL = new Set(["done", "failed", "timed_out", "cancelled", "needs_approval", "interrupted"]);

const PHASE_ORDER = [
  "scanning",
  "planning",
  "implementing",
  "testing",
  "reviewing",
  "creating_pr",
];

const PHASE_STYLE: Record<string, string> = {
  scanning: "bg-ink-700/40 text-ink-300",
  planning: "bg-sky-500/10 text-sky-300",
  implementing: "bg-syrup-500/10 text-syrup-300",
  testing: "bg-chai-500/10 text-chai-300",
  reviewing: "bg-purple-500/10 text-purple-300",
  creating_pr: "bg-green-500/10 text-green-300",
};

const STEP_DOT: Record<string, string> = {
  step: "bg-syrup-400",
  tool_call: "bg-chai-400",
  message: "bg-ink-500",
  diff: "bg-green-400",
  done: "bg-green-400",
  error: "bg-red-400",
};

function Action({ onClick, children }: { onClick: () => void; children: string }) {
  return (
    <button onClick={onClick} className="btn-ghost">
      {children}
    </button>
  );
}

function FollowUpComposer({
  task,
  followups,
  onSent,
}: {
  task: Task;
  followups: Followup[];
  onSent: () => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.postFollowup(task.id, text.trim());
      setText("");
      onSent();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to send follow-up");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="surface p-5 animate-fade-up">
      <h2 className="panel-title mb-3">Follow-up</h2>
      <p className="mb-3 text-xs leading-relaxed text-ink-500">
        Send a follow-up to resume this task&apos;s session in the same worktree and branch —
        the agent picks up where it left off.
      </p>
      <form onSubmit={submit} className="space-y-3">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={3}
          placeholder="e.g. Address the reviewer comments, then update the README…"
          className="field resize-y"
        />
        {error && <p className="text-xs text-red-400">{error}</p>}
        <div className="flex justify-end">
          <button type="submit" disabled={busy || !text.trim()} className="btn-primary">
            {busy ? "Sending…" : "Send follow-up"}
          </button>
        </div>
      </form>
      {followups.length > 0 && (
        <ol className="mt-4 space-y-2 border-t border-ink-800 pt-3">
          {followups.map((f) => (
            <li key={f.id} className="flex gap-2 text-sm">
              <span className="shrink-0 font-mono text-[11px] leading-6 text-ink-600">
                {f.created_at.slice(11, 19)}
              </span>
              <span className="text-ink-300">{f.body}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function TimelineItem({
  step,
  index,
}: {
  step: SseEvent;
  index: number;
}) {
  const dot = STEP_DOT[step.type] ?? "bg-ink-600";
  return (
    <li
      className="relative flex gap-3 animate-fade-up"
      style={{ animationDelay: `${Math.min(index * 0.02, 0.3)}s` }}
    >
      <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ring-2 ring-ink-950 ${dot}`} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[11px] text-ink-600">
            {step.ts?.slice(11, 19) ?? ""}
          </span>
          <span className="font-mono text-[11px] uppercase tracking-wide text-ink-500">
            {step.type}
          </span>
          {step.phase && (
            <span
              className={`rounded px-1.5 py-0.5 font-mono text-[10px] ${
                PHASE_STYLE[step.phase] ?? "bg-ink-700/40 text-ink-300"
              }`}
            >
              {step.phase}
            </span>
          )}
        </div>
        {step.text && <p className="mt-0.5 text-sm leading-snug text-ink-300">{step.text}</p>}
      </div>
    </li>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

export default function TaskDetail() {
  const { id } = useParams();
  const taskId = Number(id);
  const [task, setTask] = useState<Task | null>(null);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [live, setLive] = useState<SseEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [followUpPending, setFollowUpPending] = useState(false);
  const lastRunRef = useRef<number | null>(null);

  const load = useCallback(() => {
    api
      .getTask(taskId)
      .then((t) => {
        const runId = t.run?.id ?? null;
        if (lastRunRef.current !== runId) {
          // New run (or first load): drop stale live events.
          setLive([]);
          lastRunRef.current = runId;
        }
        setTask(t);
      })
      .catch((e) => setError(e.message));
    api
      .getRepos()
      .then(setRepos)
      .catch(() => {});
  }, [taskId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!task || TERMINAL.has(task.status)) return;
    const unsubscribe = taskEvents(
      taskId,
      (event) => setLive((l) => [...l, event]),
      () => {
        // stream_end: persisted steps now own the data.
        setLive([]);
        load();
      }
    );
    return unsubscribe;
  }, [taskId, task, load]);

  // After sending a follow-up, poll until a new run appears, then refresh so the
  // live SSE stream reconnects (the task flips to running once the worker starts).
  useEffect(() => {
    if (!followUpPending) return;
    let attempts = 0;
    const timer = setInterval(() => {
      attempts += 1;
      if (attempts > 40) {
        setFollowUpPending(false);
        return;
      }
      api
        .getTask(taskId)
        .then((t) => {
          const runId = t.run?.id ?? null;
          if (runId !== lastRunRef.current) {
            lastRunRef.current = runId;
            setLive([]);
            setTask(t);
            setFollowUpPending(false);
          }
        })
        .catch(() => {});
    }, 1500);
    return () => clearInterval(timer);
  }, [followUpPending, taskId]);

  if (error) return <p className="text-red-400">{error}</p>;
  if (!task) return <p className="text-ink-500">Loading…</p>;

  const repoName = repos.find((r) => r.id === task.repo_id)?.full_name ?? `repo#${task.repo_id}`;
  const steps = task.run?.steps ?? [];
  const timeline = [...steps, ...live];
  const consoleLines = timeline.filter((s) => s.type === "message" || s.type === "tool_call");

  const lastPhase = timeline.reduce<string | null>((acc, s) => s.phase ?? acc, null);
  const phaseIndex = lastPhase ? PHASE_ORDER.indexOf(lastPhase) : -1;

  return (
    <div className="space-y-6 animate-fade-up">
      <div className="flex flex-wrap items-center gap-3">
        <Link to="/" className="text-sm text-ink-500 transition-colors hover:text-syrup-300">
          ← Tasks
        </Link>
        <h1 className="text-2xl font-bold tracking-tight text-ink-100">Task #{task.id}</h1>
        <StatusBadge status={task.status} />
        <span className="mx-1 hidden h-4 w-px bg-ink-800 sm:block" />
        <span className="font-mono text-xs text-ink-500">
          {repoName}
          <span className="mx-1.5 text-ink-700">·</span>
          {task.model ?? "default model"}
        </span>
        {task.pr_number && repos.some((r) => r.id === task.repo_id) && (
          <a
            className="btn-ghost !px-3 !py-1 text-xs"
            href={`https://github.com/${repoName}/pull/${task.pr_number}`}
            target="_blank"
            rel="noreferrer"
          >
            PR #{task.pr_number} ↗
          </a>
        )}
      </div>

      <section className="surface p-6">
        <div className="flex items-center justify-between gap-4">
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-ink-100">{task.prompt}</p>
          {phaseIndex >= 0 && (
            <div className="hidden shrink-0 flex-col items-center gap-1.5 md:flex">
              <div className="flex h-10 w-10 items-center justify-center rounded-full border border-syrup-500/40 bg-syrup-500/10 font-mono text-sm text-syrup-300">
                {phaseIndex + 1}/{PHASE_ORDER.length}
              </div>
              <span className="font-mono text-[11px] text-ink-500">{PHASE_ORDER[phaseIndex]}</span>
            </div>
          )}
        </div>
        <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 border-t border-ink-800 pt-4 text-xs sm:grid-cols-4">
          <div>
            <dt className="text-ink-600">Type</dt>
            <dd className="mt-0.5 font-mono text-ink-300">{task.type}</dd>
          </div>
          <div>
            <dt className="text-ink-600">Branch</dt>
            <dd className="mt-0.5 font-mono text-ink-300">
              {task.target_branch || "—"} ← {task.source_branch || "default"}
            </dd>
          </div>
          <div>
            <dt className="text-ink-600">Timeout</dt>
            <dd className="mt-0.5 font-mono text-ink-300">{task.timeout_minutes}m</dd>
          </div>
          <div>
            <dt className="text-ink-600">Retries</dt>
            <dd className="mt-0.5 font-mono text-ink-300">{task.retry_count}</dd>
          </div>
        </dl>
      </section>

      <div className="flex flex-wrap gap-2">
        {(task.status === "running" || task.status === "queued") && (
          <Action onClick={() => api.cancelTask(task.id).then(load)}>Cancel</Action>
        )}
        {TERMINAL.has(task.status) && task.status !== "cancelled" && (
          <Action onClick={() => api.rerunTask(task.id).then(load)}>Re-run</Action>
        )}
        {task.status === "needs_approval" && (
          <Action onClick={() => api.publishTask(task.id).then(load)}>Publish</Action>
        )}
      </div>

      {task.run?.session_id && TERMINAL.has(task.status) && (
        <FollowUpComposer
          task={task}
          followups={task.followups ?? []}
          onSent={() => {
            load();
            setFollowUpPending(true);
          }}
        />
      )}

      {task.run?.artifacts && task.run.artifacts.length > 0 && (
        <section className="surface p-5 animate-fade-up">
          <h2 className="panel-title mb-3">Artifacts</h2>
          <ul className="divide-y divide-ink-800/70">
            {task.run.artifacts.map((a) => (
              <li key={a.id} className="flex items-center gap-3 py-2">
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-chai-400" />
                <a
                  href={api.artifactUrl(task.id, a.id)}
                  className="min-w-0 flex-1 truncate font-mono text-sm text-ink-200 transition-colors hover:text-syrup-300"
                >
                  {a.path}
                </a>
                <span className="shrink-0 font-mono text-[11px] text-ink-500">
                  {formatBytes(a.size)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section className="surface flex min-h-[24rem] flex-col p-5">
          <h2 className="panel-title mb-3">Timeline</h2>
          <ol className="space-y-3 overflow-y-auto pr-2 text-sm">
            {timeline.length === 0 && <li className="text-ink-600">No steps yet.</li>}
            {timeline.map((step, i) => (
              <TimelineItem key={i} step={step} index={i} />
            ))}
          </ol>
        </section>
        <section className="surface flex min-h-[24rem] flex-col p-5">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="panel-title">Console</h2>
            <span className="font-mono text-[11px] text-ink-600">
              {consoleLines.length} {consoleLines.length === 1 ? "line" : "lines"}
            </span>
          </div>
          <pre className="flex-1 overflow-y-auto whitespace-pre-wrap pr-2 font-mono text-xs leading-relaxed text-ink-300">
            {consoleLines.length === 0 ? "No output yet." : ""}
            {consoleLines.map((line, i) => (
              <div key={i} className="flex gap-2">
                <span
                  className={`shrink-0 select-none ${
                    line.type === "tool_call" ? "text-chai-500" : "text-ink-700"
                  }`}
                >
                  {line.type === "tool_call" ? "⚙" : "›"}
                </span>
                <span className={line.type === "tool_call" ? "text-chai-300" : "text-ink-300"}>
                  {line.text}
                </span>
              </div>
            ))}
          </pre>
        </section>
      </div>
    </div>
  );
}
