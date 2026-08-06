import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, taskEvents } from "../api/client";
import type { Repo, SseEvent, Task } from "../types";
import { StatusBadge } from "./Tasks";

const TERMINAL = new Set(["done", "failed", "timed_out", "cancelled", "needs_approval"]);

function Action({ onClick, children }: { onClick: () => void; children: string }) {
  return (
    <button
      onClick={onClick}
      className="rounded border border-neutral-700 bg-neutral-900 px-3 py-1 text-sm hover:bg-neutral-800"
    >
      {children}
    </button>
  );
}

export default function TaskDetail() {
  const { id } = useParams();
  const taskId = Number(id);
  const [task, setTask] = useState<Task | null>(null);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [live, setLive] = useState<SseEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .getTask(taskId)
      .then((t) => {
        setTask(t);
        setLive([]);
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
    const unsubscribe = taskEvents(taskId, (event) => setLive((l) => [...l, event]), load);
    return unsubscribe;
  }, [taskId, task, load]);

  if (error) return <p className="text-red-400">{error}</p>;
  if (!task) return <p className="text-neutral-500">Loading…</p>;

  const repoName = repos.find((r) => r.id === task.repo_id)?.full_name ?? `repo#${task.repo_id}`;
  const steps = task.run?.steps ?? [];
  const timeline = [...steps, ...live];
  const consoleLines = timeline.filter((s) => s.type === "message" || s.type === "tool_call");

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <Link to="/" className="text-sm text-blue-400">
          ← Tasks
        </Link>
        <h1 className="text-xl font-semibold">Task #{task.id}</h1>
        <StatusBadge status={task.status} />
        {task.pr_number && (
          <a
            className="text-sm text-blue-400"
            href={`https://github.com/${repoName}/pull/${task.pr_number}`}
            target="_blank"
            rel="noreferrer"
          >
            PR #{task.pr_number}
          </a>
        )}
      </div>

      <div className="space-y-1 rounded-lg border border-neutral-800 bg-neutral-900 p-4 text-sm">
        <p className="text-neutral-400">
          Repo: <span className="text-neutral-200">{repoName}</span>
        </p>
        <p className="text-neutral-400">Model: {task.model ?? "default"}</p>
        <p className="whitespace-pre-wrap text-neutral-300">{task.prompt}</p>
      </div>

      <div className="flex gap-2">
        {task.status === "running" && (
          <Action onClick={() => api.cancelTask(task.id).then(load)}>Cancel</Action>
        )}
        {TERMINAL.has(task.status) && task.status !== "cancelled" && (
          <Action onClick={() => api.rerunTask(task.id).then(load)}>Re-run</Action>
        )}
        {task.status === "needs_approval" && (
          <Action onClick={() => api.publishTask(task.id).then(load)}>Publish</Action>
        )}
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="rounded-lg border border-neutral-800 bg-neutral-900 p-4">
          <h2 className="mb-2 text-sm font-semibold text-neutral-300">Timeline</h2>
          <ol className="max-h-96 space-y-1 overflow-y-auto text-sm">
            {timeline.length === 0 && <li className="text-neutral-600">No steps yet.</li>}
            {timeline.map((step, i) => (
              <li key={i} className="flex gap-2 text-neutral-400">
                <span className="shrink-0 text-neutral-600">{step.ts?.slice(11, 19)}</span>
                <span className="text-neutral-300">{step.type}</span>
                <span className="truncate">{step.text}</span>
              </li>
            ))}
          </ol>
        </div>
        <div className="rounded-lg border border-neutral-800 bg-neutral-900 p-4">
          <h2 className="mb-2 text-sm font-semibold text-neutral-300">Console</h2>
          <pre className="max-h-96 overflow-y-auto whitespace-pre-wrap font-mono text-xs text-neutral-400">
            {consoleLines.length === 0 ? "No output yet." : ""}
            {consoleLines.map((line, i) => (
              <div key={i}>
                {line.text}
                {line.type === "tool_call" && line.text ? "" : ""}
              </div>
            ))}
          </pre>
        </div>
      </div>
    </div>
  );
}
