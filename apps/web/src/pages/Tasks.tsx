import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Repo, Task } from "../types";

const STATUS_COLORS: Record<string, string> = {
  queued: "bg-amber-500/20 text-amber-300",
  running: "bg-blue-500/20 text-blue-300",
  done: "bg-green-500/20 text-green-300",
  failed: "bg-red-500/20 text-red-300",
  timed_out: "bg-red-500/20 text-red-300",
  cancelled: "bg-neutral-500/20 text-neutral-300",
  needs_approval: "bg-purple-500/20 text-purple-300",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`rounded px-2 py-0.5 text-xs font-medium ${
        STATUS_COLORS[status] ?? "bg-neutral-500/20 text-neutral-300"
      }`}
    >
      {status}
    </span>
  );
}

function repoName(repos: Repo[], id: number): string {
  return repos.find((r) => r.id === id)?.full_name ?? `#${id}`;
}

function CreateTask({ repos, onCreated }: { repos: Repo[]; onCreated: () => void }) {
  const [repoId, setRepoId] = useState<number>(0);
  const [type, setType] = useState("freeform");
  const [prompt, setPrompt] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const effectiveRepoId = repoId || repos[0]?.id || 0;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!effectiveRepoId || !prompt.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.createTask({ repo_id: effectiveRepoId, type, prompt: prompt.trim() });
      setPrompt("");
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to create task");
    } finally {
      setBusy(false);
    }
  }

  if (repos.length === 0) {
    return <p className="text-sm text-neutral-500">No repos connected yet.</p>;
  }

  return (
    <form
      onSubmit={submit}
      className="space-y-3 rounded-lg border border-neutral-800 bg-neutral-900 p-4"
    >
      <h2 className="text-sm font-semibold text-neutral-300">New task</h2>
      <div className="flex gap-3">
        <select
          value={effectiveRepoId}
          onChange={(e) => setRepoId(Number(e.target.value))}
          className="rounded border border-neutral-700 bg-neutral-800 px-2 py-1 text-sm"
        >
          {repos.map((r) => (
            <option key={r.id} value={r.id}>
              {r.full_name}
            </option>
          ))}
        </select>
        <select
          value={type}
          onChange={(e) => setType(e.target.value)}
          className="rounded border border-neutral-700 bg-neutral-800 px-2 py-1 text-sm"
        >
          <option value="freeform">Freeform</option>
          <option value="issue_fix">Issue fix</option>
        </select>
      </div>
      <textarea
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        rows={2}
        placeholder="Instructions…"
        className="w-full rounded border border-neutral-700 bg-neutral-800 px-2 py-1 text-sm"
      />
      {error && <p className="text-xs text-red-400">{error}</p>}
      <button
        type="submit"
        disabled={busy}
        className="rounded bg-neutral-100 px-3 py-1 text-sm font-medium text-neutral-900 disabled:opacity-50"
      >
        Create
      </button>
    </form>
  );
}

export default function Tasks() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .getTasks()
      .then(setTasks)
      .catch((e) => setError(e.message));
    api
      .getRepos()
      .then(setRepos)
      .catch(() => {});
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [load]);

  return (
    <div className="space-y-4">
      <CreateTask repos={repos} onCreated={load} />
      {error && <p className="text-sm text-red-400">{error}</p>}
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-neutral-500">
            <th className="py-2">ID</th>
            <th>Status</th>
            <th>Repo</th>
            <th>Prompt</th>
            <th>PR</th>
            <th>Updated</th>
          </tr>
        </thead>
        <tbody>
          {tasks.map((t) => (
            <tr key={t.id} className="border-t border-neutral-800 hover:bg-neutral-900">
              <td className="py-2">
                <Link to={`/tasks/${t.id}`} className="text-blue-400">
                  {t.id}
                </Link>
              </td>
              <td>
                <StatusBadge status={t.status} />
              </td>
              <td className="text-neutral-300">{repoName(repos, t.repo_id)}</td>
              <td className="max-w-xs truncate text-neutral-400">{t.prompt}</td>
              <td>
                {t.pr_number ? (
                  <a
                    className="text-blue-400"
                    href={`https://github.com/${repoName(repos, t.repo_id)}/pull/${t.pr_number}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    #{t.pr_number}
                  </a>
                ) : (
                  <span className="text-neutral-600">–</span>
                )}
              </td>
              <td className="text-neutral-500">{new Date(t.updated_at).toLocaleTimeString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
