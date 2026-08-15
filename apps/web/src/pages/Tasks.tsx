import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { StatusBadge } from "../components/StatusBadge";
import type { Account, CatalogAgent, GithubContext, Repo, Task } from "../types";

function repoName(repos: Repo[], id: number): string {
  return repos.find((r) => r.id === id)?.full_name ?? `repo#${id}`;
}

function repoById(repos: Repo[], id: number): Repo | undefined {
  return repos.find((r) => r.id === id);
}

/** Server emits naive-UTC timestamps; treat them as UTC so relative times are right. */
function timeAgo(iso: string): string {
  const parsed = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  if (Number.isNaN(parsed.getTime())) return "—";
  const s = Math.max(0, (Date.now() - parsed.getTime()) / 1000);
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

function Select({
  label,
  value,
  onChange,
  children,
  placeholder,
}: {
  label: string;
  value: string | number;
  onChange: (v: string) => void;
  children: React.ReactNode;
  placeholder?: string;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium text-ink-400">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="field"
      >
        {placeholder !== undefined && <option value="">{placeholder}</option>}
        {children}
      </select>
    </label>
  );
}

function CreateTask({
  repos,
  accounts,
  onCreated,
}: {
  repos: Repo[];
  accounts: Account[];
  onCreated: () => void;
}) {
  const [repoId, setRepoId] = useState<number>(0);
  const [type, setType] = useState("freeform");
  const [prompt, setPrompt] = useState("");
  const [sourceBranch, setSourceBranch] = useState("");
  const [targetBranch, setTargetBranch] = useState("");
  const [agentId, setAgentId] = useState("");
  const [model, setModel] = useState("");
  const [patName, setPatName] = useState("");
  const [issueNumber, setIssueNumber] = useState("");
  const [prNumber, setPrNumber] = useState("");
  const [publishMode, setPublishMode] = useState<"auto" | "manual" | "">("");
  const [context, setContext] = useState<GithubContext | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [agentCli, setAgentCli] = useState("opencode");
  const [agents, setAgents] = useState<CatalogAgent[]>([]);
  const [reviewers, setReviewers] = useState<string[]>([]);
  const [envVars, setEnvVars] = useState<string[]>([]);
  const [availableEnvVars, setAvailableEnvVars] = useState<{ name: string; masked: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const effectiveRepoId = repoId || repos[0]?.id || 0;
  const repo = repoById(repos, effectiveRepoId);

  function accountLabel(name: string | null | undefined): string {
    if (!name) return "Unknown account";
    return accounts.find((a) => a.name === name)?.login ?? name;
  }

  function selectRepo(id: number) {
    setRepoId(id);
    const r = repoById(repos, id);
    setPatName(r?.pat_name ?? "");
    setEnvVars([]);
  }

  useEffect(() => {
    api
      .getModels()
      .then((m) => {
        setModels(m.models ?? []);
        setAgentCli(m.cli || "opencode");
      })
      .catch(() => {});
    api
      .getAgents(true)
      .then((a) => setAgents(a ?? []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!repo) return;
    let cancelled = false;
    api
      .getEnvVars(repo.id)
      .then((vars) => {
        if (cancelled) return;
        setAvailableEnvVars(
          vars
            .filter((v) => v.repo_id === null || v.repo_id === repo.id)
            .map((v) => ({ name: v.name, masked: v.masked }))
        );
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveRepoId, repo?.full_name]);

  useEffect(() => {
    if (!repo) return;
    let cancelled = false;
    api
      .getGithubContext(repo.full_name, repo.pat_name ?? undefined)
      .then((c) => {
        if (cancelled) return;
        setContext(c);
        if (c.branches.length > 0) {
          const def = repo.default_branch && c.branches.includes(repo.default_branch)
            ? repo.default_branch
            : c.branches[0];
          setSourceBranch(def);
          setTargetBranch(def);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveRepoId, repo?.full_name]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!effectiveRepoId || !prompt.trim()) return;
    if (type === "issue_fix" && !issueNumber) {
      setError("Pick the issue to fix.");
      return;
    }
    if (type === "pr_review" && !prNumber) {
      setError("Pick the pull request to review.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.createTask({
        repo_id: effectiveRepoId,
        type,
        prompt: prompt.trim(),
        source_branch: sourceBranch || undefined,
        target_branch: targetBranch || undefined,
        agent_id: agentId || undefined,
        model: model || undefined,
        pat_name: patName || undefined,
        issue_number: issueNumber ? Number(issueNumber) : undefined,
        pr_number: prNumber ? Number(prNumber) : undefined,
        publish_mode: publishMode === "" ? undefined : publishMode,
        reviewers: reviewers.length > 0 ? reviewers : undefined,
        env_vars: envVars,
      });
      setPrompt("");
      setIssueNumber("");
      setPrNumber("");
      setEnvVars([]);
      setAgentId("");
      setReviewers([]);
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
        <span className="eyebrow">{agentCli} · runs in a local worktree</span>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-ink-400">Repository</span>
          <select value={effectiveRepoId} onChange={(e) => selectRepo(Number(e.target.value))} className="field">
            {(() => {
              const groups = new Map<string, Repo[]>();
              for (const r of repos) {
                const key = r.pat_name ?? "";
                if (!groups.has(key)) groups.set(key, []);
                groups.get(key)!.push(r);
              }
              return [...groups.entries()].map(([key, list]) => (
                <optgroup key={key} label={accountLabel(key || null)}>
                  {list.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.full_name}
                    </option>
                  ))}
                </optgroup>
              ));
            })()}
          </select>
        </label>
        <Select label="Task type" value={type} onChange={setType}>
          {TASK_TYPES.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </Select>
      </div>

      {(type === "issue_fix" || type === "pr_review" || type === "freeform") && context && (
        <div className="grid gap-4 sm:grid-cols-2">
          {type === "issue_fix" && (
            <Select
              label="Issue"
              value={issueNumber}
              onChange={setIssueNumber}
              placeholder={context.issues.length ? "Select an issue…" : "No open issues"}
            >
              {context.issues.map((i) => (
                <option key={i.number} value={i.number}>
                  #{i.number} — {i.title}
                </option>
              ))}
            </Select>
          )}
          {type !== "issue_fix" && (
            <Select
              label={type === "pr_review" ? "Pull request" : "Link PR (optional)"}
              value={prNumber}
              onChange={setPrNumber}
              placeholder={context.prs.length ? "Select a PR…" : "No open PRs"}
            >
              {context.prs.map((p) => (
                <option key={p.number} value={p.number}>
                  #{p.number} — {p.title}
                </option>
              ))}
            </Select>
          )}
        </div>
      )}

      {type === "issue_fix" ? (
        <div className="grid gap-4 sm:grid-cols-2">
          <Select
            label="Target branch (worktree base / PR base)"
            value={targetBranch}
            onChange={setTargetBranch}
            placeholder={context ? (context.branches.length ? "default" : "no branches") : "loading…"}
          >
            {(context?.branches ?? []).map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </Select>
        </div>
      ) : type === "pr_review" ? null : (
        <div className="grid gap-4 sm:grid-cols-2">
          <Select
            label="Source branch"
            value={sourceBranch}
            onChange={setSourceBranch}
            placeholder={context ? (context.branches.length ? "default" : "no branches") : "loading…"}
          >
            {(context?.branches ?? []).map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </Select>
          <Select
            label="Target branch (PR base)"
            value={targetBranch}
            onChange={setTargetBranch}
            placeholder={context ? (context.branches.length ? "default" : "no branches") : "loading…"}
          >
            {(context?.branches ?? []).map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </Select>
        </div>
      )}

      {type === "pr_review" && agents.filter((a) => a.kind === "reviewer").length > 0 && (
        <fieldset>
          <legend className="mb-1.5 block text-xs font-medium text-ink-400">
            Reviewers (catalog agents, kind reviewer)
          </legend>
          <div className="flex flex-wrap gap-2">
            {agents
              .filter((a) => a.kind === "reviewer")
              .map((a) => {
                const checked = reviewers.includes(a.id);
                return (
                  <label
                    key={a.id}
                    className={`inline-flex cursor-pointer items-center gap-1.5 rounded-full border px-3 py-1 font-mono text-xs transition-colors ${
                      checked
                        ? "border-syrup-500/60 bg-syrup-500/10 text-syrup-300"
                        : "border-ink-800 text-ink-400 hover:border-ink-600"
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() =>
                        setReviewers((prev) =>
                          checked ? prev.filter((n) => n !== a.id) : [...prev, a.id]
                        )
                      }
                      className="hidden"
                    />
                    {a.name} ({a.id})
                  </label>
                );
              })}
          </div>
          <p className="mt-1.5 text-[11px] text-ink-500">
            Each reviewer runs its own review task on this PR and posts its comments.
          </p>
        </fieldset>
      )}

      <div className="grid gap-4 sm:grid-cols-4">
        <Select
          label="Agent"
          value={agentId}
          onChange={setAgentId}
          placeholder="Default build agent"
        >
          {agents.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name} ({a.id})
            </option>
          ))}
        </Select>
        <Select label="Model" value={model} onChange={setModel} placeholder="default model">
          {models.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </Select>
        <Select
          label="Credentials"
          value={patName}
          onChange={setPatName}
          placeholder="Inherit repo account"
        >
          {accounts.map((a) => (
            <option key={a.name} value={a.name}>
              {a.login ?? a.name} ({a.masked})
            </option>
          ))}
        </Select>
        <Select
          label="Publish mode"
          value={publishMode}
          onChange={(v) => setPublishMode(v as "auto" | "manual" | "")}
          placeholder="Auto (by type)"
        >
          <option value="auto">Auto — publish when done</option>
          <option value="manual">Manual — I publish</option>
        </Select>
      </div>

      {availableEnvVars.length > 0 && (
        <fieldset>
          <legend className="mb-1.5 block text-xs font-medium text-ink-400">
            Environment variables
          </legend>
          <div className="flex flex-wrap gap-2">
            {availableEnvVars.map((v) => {
              const checked = envVars.includes(v.name);
              return (
                <label
                  key={v.name}
                  className={`inline-flex cursor-pointer items-center gap-1.5 rounded-full border px-3 py-1 font-mono text-xs transition-colors ${
                    checked
                      ? "border-syrup-500/60 bg-syrup-500/10 text-syrup-300"
                      : "border-ink-800 text-ink-400 hover:border-ink-600"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() =>
                      setEnvVars((prev) =>
                        checked ? prev.filter((n) => n !== v.name) : [...prev, v.name]
                      )
                    }
                    className="hidden"
                  />
                  {v.name}
                </label>
              );
            })}
          </div>
          <p className="mt-1.5 text-[11px] text-ink-500">
            These variables are injected into the agent&apos;s environment for this task.
          </p>
        </fieldset>
      )}

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
        <button
          type="submit"
          disabled={busy || !effectiveRepoId || !prompt.trim()}
          className="btn-primary"
        >
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
  { id: "failed", label: "Failed", test: (s: string) => s === "failed" || s === "timed_out" || s === "interrupted" },
  { id: "review", label: "Review", test: (s: string) => s === "needs_approval" },
] as const;

type FilterId = (typeof FILTERS)[number]["id"];

type SortKey = "id" | "updated_at" | "status";
const PAGE_SIZE = 10;

function GhLink({
  repo,
  kind,
  number,
}: {
  repo: string;
  kind: "pull" | "issues";
  number: number;
}) {
  return (
    <a
      className="font-mono text-xs text-syrup-400 hover:text-syrup-300"
      href={`https://github.com/${repo}/${kind}/${number}`}
      target="_blank"
      rel="noreferrer"
    >
      {kind === "pull" ? "#" : "issue "}#{number}
    </a>
  );
}

export default function Tasks() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<FilterId>("all");
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("updated_at");
  const [sortDesc, setSortDesc] = useState(true);
  const [page, setPage] = useState(0);

  const load = useCallback(() => {
    api
      .getTasks()
      .then(setTasks)
      .catch((e) => setError(e.message));
    api
      .getRepos()
      .then(setRepos)
      .catch(() => {});
    api
      .getTokens()
      .then((t) => setAccounts(t.accounts ?? []))
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
    const q = query.trim().toLowerCase();
    const list = tasks.filter((t) => {
      if (!test(t.status)) return false;
      if (!q) return true;
      return (
        String(t.id).includes(q) ||
        t.prompt.toLowerCase().includes(q) ||
        (t.repo_full_name ?? repoName(repos, t.repo_id)).toLowerCase().includes(q)
      );
    });
    list.sort((a, b) => {
      const av = sortKey === "status" ? a.status : sortKey === "id" ? a.id : a.updated_at;
      const bv = sortKey === "status" ? b.status : sortKey === "id" ? b.id : b.updated_at;
      const cmp = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv));
      return sortDesc ? -cmp : cmp;
    });
    return list;
  }, [tasks, filter, query, sortKey, sortDesc, repos]);

  const pageCount = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  const pageRows = visible.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDesc((d) => !d);
    } else {
      setSortKey(key);
      setSortDesc(key !== "id");
    }
    setPage(0);
  }

  function selectFilter(f: FilterId) {
    setFilter(f);
    setPage(0);
  }

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

      <CreateTask repos={repos} accounts={accounts} onCreated={load} />

      {error && <p className="text-sm text-red-400">{error}</p>}

      <section className="surface animate-fade-up" style={{ animationDelay: "0.1s" }}>
        <div className="flex flex-wrap items-center gap-2 border-b border-ink-800 px-4 py-3">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              onClick={() => selectFilter(f.id)}
              className={`rounded-full px-3.5 py-1 text-sm transition-colors ${
                filter === f.id
                  ? "bg-syrup-500 text-ink-950 font-semibold"
                  : "text-ink-400 hover:bg-ink-850 hover:text-ink-100"
              }`}
            >
              {f.label}
            </button>
          ))}
          <input
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setPage(0);
            }}
            placeholder="Search tasks…"
            className="field ml-auto w-48 !py-1 text-sm"
          />
        </div>

        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wider text-ink-500">
              <th className="cursor-pointer select-none px-4 pt-3 pb-2 font-medium hover:text-ink-300" onClick={() => toggleSort("id")}>
                ID {sortKey === "id" ? (sortDesc ? "↓" : "↑") : ""}
              </th>
              <th className="cursor-pointer select-none px-4 pb-2 font-medium hover:text-ink-300" onClick={() => toggleSort("status")}>
                Status {sortKey === "status" ? (sortDesc ? "↓" : "↑") : ""}
              </th>
              <th className="px-4 pb-2 font-medium">Repo</th>
              <th className="px-4 pb-2 font-medium">Prompt</th>
              <th className="px-4 pb-2 font-medium">PR / Issues</th>
              <th className="cursor-pointer select-none px-4 pb-2 font-medium hover:text-ink-300" onClick={() => toggleSort("updated_at")}>
                Updated {sortKey === "updated_at" ? (sortDesc ? "↓" : "↑") : ""}
              </th>
            </tr>
          </thead>
          <tbody>
            {pageRows.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-ink-600">
                  No tasks{filter !== "all" ? ` in “${filter}”` : ""}
                  {query ? " matching your search" : ""} yet.
                </td>
              </tr>
            )}
            {pageRows.map((t) => {
              const rn = t.repo_full_name ?? repoName(repos, t.repo_id);
              return (
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
                    <RepoChip name={rn} />
                  </td>
                  <td className="max-w-xs truncate px-4 py-3 text-ink-300">{t.prompt}</td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap items-center gap-1.5">
                      {(t.prs?.length ? t.prs : t.pr_number ? [t.pr_number] : []).map((n) => (
                        <GhLink key={`p${n}`} repo={rn} kind="pull" number={n} />
                      ))}
                      {(t.issues ?? []).map((n) => (
                        <GhLink key={`i${n}`} repo={rn} kind="issues" number={n} />
                      ))}
                      {!t.prs?.length && !t.issues?.length && (
                        <span className="text-ink-600">–</span>
                      )}
                    </div>
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-ink-500">
                    {timeAgo(t.updated_at)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {pageCount > 1 && (
          <div className="flex items-center justify-end gap-2 border-t border-ink-800 px-4 py-3 text-xs text-ink-500">
            <button
              disabled={page === 0}
              onClick={() => setPage((p) => p - 1)}
              className="btn-ghost !px-2 !py-1 disabled:opacity-40"
            >
              ← Prev
            </button>
            <span className="font-mono">
              {page + 1} / {pageCount}
            </span>
            <button
              disabled={page >= pageCount - 1}
              onClick={() => setPage((p) => p + 1)}
              className="btn-ghost !px-2 !py-1 disabled:opacity-40"
            >
              Next →
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
