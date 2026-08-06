import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { StatusBadge } from "../components/StatusBadge";
import type { Repo, Task } from "../types";

function repoName(repos: Repo[], id: number): string {
  return repos.find((r) => r.id === id)?.full_name ?? `repo#${id}`;
}

function timeAgo(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function RepoChip({ name }: { name: string }) {
  const [owner, repo] = name.split("/");
  return (
    <span className="inline-flex items-center gap-1.5 font-mono text-xs text-ink-200">
      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-chai-500" />
      <span className="text-ink-500">{owner}/</span>
      {repo}
    </span>
  );
}

const TASK_TYPES = [
  { value: "freeform", label: "Freeform" },
  { value: "issue_fix", label: "Issue fix" },
  { value: "pr_review", label: "Review PR" },
  { value: "screen_finding", label: "Screen finding" },
];

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
    return (
      <div className="surface flex flex-col items-start gap-3 p-6 animate-fade-up">
        <h2 className="panel-title">New task</h2>
        <p className="text-sm text-ink-400">
          No repos connected yet — connect one from the{" "}
          <Link to="/repos" className="link">
            Repos
          </Link>{" "}
          page first.
        </p>
      </div>
    );
  }

  return (
    <form
      onSubmit={submit}
      className="surface space-y-4 p-6 animate-fade-up"
      style={{ animationDelay: "0.05s" }}
    >
      <div className="flex items-baseline justify-between">
        <h2 className="panel-title">New task</h2>
        <span className="eyebrow">opencode · local worktree</span>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-ink-400">Repository</span>
          <select
            value={effectiveRepoId}
            onChange={(e) => setRepoId(Number(e.target.value))}
            className="field"
          >
            {repos.map((r) => (
              <option key={r.id} value={r.id}>
                {r.full_name}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-ink-400">Task type</span>
          <select value={type} onChange={(e) => setType(e.target.value)} className="field">
            {TASK_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <label className="block">
        <span className="mb-1.5 block text-xs font-medium text-ink-400">Instructions</span>
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={3}
          placeholder="Instructions…"
          className="field resize-y"
        />
      </label>

      {error && <p className="text-xs text-red-400">{error}</p>}

      <div className="flex justify-end">
        <button type="submit" disabled={busy || !effectiveRepoId} className="btn-primary">
          {busy ? "Creating…" : "Create"}
        </button>
      </div>
    </form>
  );
}

const FILTERS = [
  { id: "all", label: "All", test: () => true },
  { id: "running", label: "Running", test: (s: string) => s === "queued" || s === "running" },
  { id: "done", label: "Done", test: (s: string) => s === "done" },
  { id: "failed", label: "Failed", test: (s: string) => s === "failed" || s === "timed_out" },
  { id: "review", label: "Review", test: (s: string) => s === "needs_approval" },
] as const;

type FilterId = (typeof FILTERS)[number]["id"];

export default function Tasks() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<FilterId>("all");

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

  const stats = useMemo(() => {
    const running = tasks.filter((t) => t.status === "queued" || t.status === "running").length;
    const done = tasks.filter((t) => t.status === "done").length;
    const review = tasks.filter((t) => t.status === "needs_approval").length;
    return { total: tasks.length, running, done, review };
  }, [tasks]);

  const visible = useMemo(() => {
    const test = FILTERS.find((f) => f.id === filter)!.test;
    return tasks.filter((t) => test(t.status));
  }, [tasks, filter]);

  const statCards = [
    { label: "Total", value: stats.total, accent: "text-ink-100" },
    { label: "Running", value: stats.running, accent: "text-syrup-300" },
    { label: "Done", value: stats.done, accent: "text-green-300" },
    { label: "Needs review", value: stats.review, accent: "text-purple-300" },
  ];

  return (
    <div className="space-y-6">
      <header className="animate-fade-up">
        <h1 className="text-3xl font-bold tracking-tight text-ink-100">Tasks</h1>
        <p className="mt-1 text-sm text-ink-500">
          Your agent queue — what&apos;s running, what&apos;s done, what needs a decision.
        </p>
      </header>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 animate-fade-up" style={{ animationDelay: "0.05s" }}>
        {statCards.map((c) => (
          <div key={c.label} className="surface px-5 py-4">
            <p className="text-xs font-medium uppercase tracking-wider text-ink-500">{c.label}</p>
            <p className={`mt-1 font-mono text-3xl font-medium tabular-nums ${c.accent}`}>
              {c.value}
            </p>
          </div>
        ))}
      </div>

      <CreateTask repos={repos} onCreated={load} />

      {error && <p className="text-sm text-red-400">{error}</p>}

      <section className="surface animate-fade-up" style={{ animationDelay: "0.1s" }}>
        <div className="flex flex-wrap items-center gap-2 border-b border-ink-800 px-4 py-3">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              onClick={() => setFilter(f.id)}
              className={`rounded-full px-3.5 py-1 text-sm transition-colors ${
                filter === f.id
                  ? "bg-syrup-500 text-ink-950 font-semibold"
                  : "text-ink-400 hover:bg-ink-850 hover:text-ink-100"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>

        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wider text-ink-500">
              <th className="px-4 pt-3 pb-2 font-medium">ID</th>
              <th className="px-4 pb-2 font-medium">Status</th>
              <th className="px-4 pb-2 font-medium">Repo</th>
              <th className="px-4 pb-2 font-medium">Prompt</th>
              <th className="px-4 pb-2 font-medium">PR</th>
              <th className="px-4 pb-2 font-medium">Updated</th>
            </tr>
          </thead>
          <tbody>
            {visible.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-ink-600">
                  No tasks{filter !== "all" ? ` in “${filter}”` : ""} yet.
                </td>
              </tr>
            )}
            {visible.map((t) => (
              <tr key={t.id} className="border-t border-ink-800/70 transition-colors hover:bg-ink-875/50">
                <td className="px-4 py-3">
                  <Link to={`/tasks/${t.id}`} className="font-mono text-syrup-400 hover:text-syrup-300">
                    #{t.id}
                  </Link>
                </td>
                <td className="px-4 py-3">
                  <StatusBadge status={t.status} />
                </td>
                <td className="px-4 py-3">
                  <RepoChip name={repoName(repos, t.repo_id)} />
                </td>
                <td className="max-w-xs truncate px-4 py-3 text-ink-300">{t.prompt}</td>
                <td className="px-4 py-3">
                  {t.pr_number ? (
                    <a
                      className="font-mono text-xs text-syrup-400 hover:text-syrup-300"
                      href={`https://github.com/${repoName(repos, t.repo_id)}/pull/${t.pr_number}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      #{t.pr_number}
                    </a>
                  ) : (
                    <span className="text-ink-600">–</span>
                  )}
                </td>
                <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-ink-500">
                  {timeAgo(t.updated_at)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
