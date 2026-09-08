import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { AttentionBadge } from "../components/AttentionBadge";
import { BrewHouse } from "../components/brew/BrewHouse";
import type { SnackKind } from "../components/brew/snacks";
import { DepBadges } from "../components/DepBadges";
import { RunningCard } from "../components/RunningCard";
import { StatusBadge } from "../components/StatusBadge";
import { useBackends } from "../hooks/useBackends";
import type { Account, CatalogAgent, GithubContext, Repo, SettingsMap, Task } from "../types";

function repoName(repos: Repo[], id: number): string {
  return repos.find((r) => r.id === id)?.full_name ?? `repo#${id}`;
}

function repoById(repos: Repo[], id: number): Repo | undefined {
  return repos.find((r) => r.id === id);
}

/** Server emits timestamps with a zone offset (or naive UTC); parse as-is
 * when a designator is present so offset strings don't become Invalid Date. */
function timeAgo(iso: string): string {
  const zoned = /([zZ]|[+-]\d{2}:?\d{2})$/.test(iso);
  const parsed = new Date(zoned ? iso : `${iso}Z`);
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
];

function Select({
  label,
  value,
  onChange,
  children,
  placeholder,
  disabled,
}: {
  label: string;
  value: string | number;
  onChange: (v: string) => void;
  children: React.ReactNode;
  placeholder?: string;
  disabled?: boolean;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium text-ink-400">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="field disabled:opacity-50"
        disabled={disabled}
      >
        {placeholder !== undefined && <option value="">{placeholder}</option>}
        {children}
      </select>
    </label>
  );
}

/** Fields a task row can push back into the form via the Clone button. */
export interface TaskPrefill {
  repoId?: number;
  type?: string;
  prompt?: string;
  sourceBranch?: string;
  targetBranch?: string;
  agentId?: string;
  cli?: string;
  model?: string;
  publishMode?: "auto" | "manual" | "";
  prNumber?: string;
  issueNumber?: string;
  patName?: string;
  envVars?: string[];
}

const TASK_DEFAULTS_KEY = "jalebi-task-defaults";

function loadTaskDefaults(): Partial<
  Pick<TaskPrefill, "repoId" | "type" | "agentId" | "cli" | "model" | "publishMode">
> {
  try {
    const raw = localStorage.getItem(TASK_DEFAULTS_KEY);
    return raw ? (JSON.parse(raw) as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

function CreateTask({
  repos,
  accounts,
  onCreated,
  prefill,
}: {
  repos: Repo[];
  accounts: Account[];
  onCreated: (id?: number) => void;
  prefill: TaskPrefill | null;
}) {
  const stored = useMemo(() => loadTaskDefaults(), []);
  const [repoId, setRepoId] = useState<number>(prefill?.repoId ?? 0);
  const [type, setType] = useState(prefill?.type ?? stored.type ?? "freeform");
  const [prompt, setPrompt] = useState(prefill?.prompt ?? "");
  const [sourceBranch, setSourceBranch] = useState(prefill?.sourceBranch ?? "");
  const [targetBranch, setTargetBranch] = useState(prefill?.targetBranch ?? "");
  const [agentId, setAgentId] = useState(prefill?.agentId ?? stored.agentId ?? "");
  const [model, setModel] = useState(prefill?.model ?? stored.model ?? "");
  const [patName, setPatName] = useState(prefill?.patName ?? "");
  const [issueNumber, setIssueNumber] = useState(prefill?.issueNumber ?? "");
  const [prNumber, setPrNumber] = useState(prefill?.prNumber ?? "");
  const [publishMode, setPublishMode] = useState<"auto" | "manual" | "">(
    prefill?.publishMode ?? stored.publishMode ?? ""
  );
  const [context, setContext] = useState<GithubContext | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [agentCli, setAgentCli] = useState<string | null>(prefill?.cli ?? stored.cli ?? null);
  const [settings, setSettings] = useState<SettingsMap | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);

  // Clone remounts with branches baked into the initial state — the context
  // fetch must not clobber them back to the repo default on arrival. The
  // flag is consumed by the first context load; picking another repo clears
  // the branches and re-arms defaulting.
  const preserveBranches = useRef(!!(prefill?.sourceBranch || prefill?.targetBranch));
  const backendOptions = useBackends();
  const [agents, setAgents] = useState<CatalogAgent[]>([]);
  const [reviewers, setReviewers] = useState<string[]>([]);
  const [envVars, setEnvVars] = useState<string[]>(prefill?.envVars ?? []);
  const [availableEnvVars, setAvailableEnvVars] = useState<{ name: string; masked: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const settingsLoading = agentCli === null;
  // The remembered repo applies without an effect: an explicitly picked repo
  // wins, then the remembered one (when still connected), then the first.
  const storedRepoValid =
    stored.repoId && repos.some((r) => r.id === stored.repoId) ? stored.repoId : 0;
  const effectiveRepoId = repoId || storedRepoValid || repos[0]?.id || 0;
  const repo = repoById(repos, effectiveRepoId);
  // Credentials likewise fall back to the effective repo's account until the
  // user picks a repo or an account explicitly.
  const effectivePatName = patName || repo?.pat_name || "";

  // Fork-aware fix flow: a freeform task linked to a fork PR can be based on
  // the PR head commit (which never exists on origin) instead of an origin
  // branch. The sentinel `pr/<N>/head` tells the backend to fetch
  // `refs/pull/<N>/head` for the worktree and to push back to the fork.
  const selectedPr = context?.prs.find((p) => p.number === Number(prNumber)) ?? null;
  const prHeadValue = selectedPr ? `pr/${selectedPr.number}/head` : "";
  const prHeadMissingOnOrigin =
    !!selectedPr?.head && !(context?.branches.includes(selectedPr.head) ?? true);
  const showPrHeadOption =
    type === "freeform" &&
    !!selectedPr &&
    (!!selectedPr.is_fork || prHeadMissingOnOrigin || !!selectedPr.head_repo);
  const isPrHeadSelected = !!prHeadValue && sourceBranch === prHeadValue;

  // Review tasks carry their brief with the agent — instructions optional.
  const promptRequired = type !== "pr_review";

  const agentName = agents.find((a) => a.id === agentId)?.name ?? null;
  const advancedSummary = [
    agentCli ?? "…",
    model || "default model",
    agentName ?? "default agent",
    publishMode === "manual" ? "manual publish" : "auto publish",
    effectivePatName ? `as ${accountLabel(effectivePatName)}` : null,
    envVars.length > 0 ? `${envVars.length} env` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  const STARTERS: Record<string, string[]> = {
    freeform: ["Fix the failing tests in …", "Implement …", "Refactor … for clarity"],
    issue_fix: ["Fix with minimal changes", "Fix and add a regression test"],
    pr_review: ["Focus on security issues", "Focus on performance", "Check tests and edge cases"],
  };

  function insertStarter(s: string) {
    setPrompt((cur) => (cur.trim() ? `${cur.trim()}\n${s}` : s));
  }

  function usePrHeadBase() {
    if (!selectedPr) return;
    setSourceBranch(`pr/${selectedPr.number}/head`);
    if (selectedPr.base) setTargetBranch(selectedPr.base);
  }

  function accountLabel(name: string | null | undefined): string {
    if (!name) return "Unknown account";
    return accounts.find((a) => a.name === name)?.login ?? name;
  }

  function selectRepo(id: number) {
    setRepoId(id);
    const r = repoById(repos, id);
    setPatName(r?.pat_name ?? "");
    setEnvVars([]);
    // A new repo means new branches — drop the old picks so the context
    // load below re-defaults to this repo's default branch.
    setSourceBranch("");
    setTargetBranch("");
    preserveBranches.current = false;
  }

  function selectPr(num: string) {
    setPrNumber(num);
    // Intuitive default: picking a PR bases the work on its head branch and
    // targets its base branch — but only when those branches exist on
    // origin. A fork head missing from origin keeps the current base; the
    // PR-head hint below offers the `pr/<N>/head` sentinel instead.
    if (type !== "freeform" || !num || !context) return;
    const pr = context.prs.find((p) => p.number === Number(num)) ?? null;
    if (!pr) return;
    if (pr.head && context.branches.includes(pr.head)) setSourceBranch(pr.head);
    if (pr.base && context.branches.includes(pr.base)) setTargetBranch(pr.base);
  }

  useEffect(() => {
    // The Backend select defaults to the global default_backend, unless a
    // last-used backend was remembered — the Model dropdown follows the
    // backend selected in THIS form. When the selected backend is the
    // default backend and a default model is configured, the Model
    // selection defaults to it (unless the user already picked one).
    api
      .getSettings()
      .then((s) => {
        setSettings(s);
        setAgentCli((cur) => cur ?? s.default_backend ?? "opencode");
      })
      .catch(() => {
        setAgentCli((cur) => cur ?? "opencode");
      });
    api
      .getAgents(true)
      .then((a) => {
        const list = a ?? [];
        setAgents(list);
        // Drop a remembered agent that no longer exists in the catalog.
        setAgentId((cur) => (cur && list.some((x) => x.id === cur) ? cur : ""));
      })
      .catch(() => {});
  }, []);



  useEffect(() => {
    let cancelled = false;
    if (agentCli === null) return;
    api
      .getModels(agentCli || undefined)
      .then((m) => {
        if (cancelled) return;
        setModels(m.models ?? []);
        if (agentCli === settings?.default_backend && settings?.default_model) {
          setModel((cur) => cur || settings.default_model);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [agentCli, settings?.default_backend, settings?.default_model]);

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
        if (c.branches.length > 0 && !preserveBranches.current) {
          const def =
            repo.default_branch && c.branches.includes(repo.default_branch)
              ? repo.default_branch
              : c.branches[0];
          setSourceBranch(def);
          setTargetBranch(def);
        }
        preserveBranches.current = false;
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveRepoId, repo?.full_name]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    // (promptRequired is computed above: reviews don't need instructions.
    // The backend still requires a non-empty prompt, so an empty review box
    // sends a `Review PR #N` default.)
    const trimmed = prompt.trim();
    if (!effectiveRepoId || (promptRequired && !trimmed)) return;
    if (agentCli === null) return; // settings still loading — refuse submit
    if (type === "issue_fix" && !issueNumber) {
      setError("Pick the issue to fix.");
      return;
    }
    if (type === "pr_review" && !prNumber) {
      setError("Pick the pull request to review.");
      return;
    }
    const effectivePrompt =
      trimmed || (type === "pr_review" && prNumber ? `Review PR #${prNumber}.` : trimmed);
    if (!effectivePrompt) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.createTask({
        repo_id: effectiveRepoId,
        type,
        prompt: effectivePrompt,
        source_branch: sourceBranch || undefined,
        target_branch: targetBranch || undefined,
        agent_id: agentId || undefined,
        cli: agentCli || undefined,
        model: model || undefined,
        pat_name: effectivePatName || undefined,
        issue_number: issueNumber ? Number(issueNumber) : undefined,
        pr_number: prNumber ? Number(prNumber) : undefined,
        publish_mode: publishMode === "" ? undefined : publishMode,
        reviewers: reviewers.length > 0 ? reviewers : undefined,
        env_vars: envVars,
      });
      // Remember last-used settings so the next task starts where this one did.
      try {
        localStorage.setItem(
          TASK_DEFAULTS_KEY,
          JSON.stringify({
            repoId: effectiveRepoId,
            type,
            agentId,
            cli: agentCli,
            model,
            publishMode,
          })
        );
      } catch {
        /* private-mode storage — non-fatal */
      }
      setPrompt("");
      setIssueNumber("");
      setPrNumber("");
      setEnvVars([]);
      setAgentId("");
      setReviewers([]);
      onCreated(created.id);
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
      id="new-task-form"
      className="surface space-y-4 p-6 animate-fade-up"
      style={{ animationDelay: "0.05s" }}
    >
      <div className="flex items-baseline justify-between">
        <h2 className="panel-title">New task</h2>
        <span className="eyebrow">
          {agentCli === null ? "Loading defaults…" : `${agentCli} · runs in a local worktree`}
        </span>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-ink-400">Repository</span>
          <select
            value={effectiveRepoId}
            onChange={(e) => selectRepo(Number(e.target.value))}
            className="field"
          >
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
              onChange={selectPr}
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
            placeholder={
              context ? (context.branches.length ? "default" : "no branches") : "loading…"
            }
          >
            {(context?.branches ?? []).map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </Select>
        </div>
      ) : type === "pr_review" ? null : (
        <div className="space-y-3">
          <div className="grid gap-4 sm:grid-cols-2">
            <Select
              label="Source branch"
              value={sourceBranch}
              onChange={setSourceBranch}
              placeholder={
                context ? (context.branches.length ? "default" : "no branches") : "loading…"
              }
            >
              {(context?.branches ?? []).map((b) => (
                <option key={b} value={b}>
                  {b}
                </option>
              ))}
              {(showPrHeadOption || isPrHeadSelected) && selectedPr && (
                <option value={prHeadValue}>
                  PR #{selectedPr.number} head
                  {selectedPr.head_repo
                    ? ` (${selectedPr.head_repo}:${selectedPr.head})`
                    : ` (${selectedPr.head})`}
                </option>
              )}
            </Select>
            <Select
              label="Target branch (PR base)"
              value={targetBranch}
              onChange={setTargetBranch}
              placeholder={
                context ? (context.branches.length ? "default" : "no branches") : "loading…"
              }
            >
              {(context?.branches ?? []).map((b) => (
                <option key={b} value={b}>
                  {b}
                </option>
              ))}
            </Select>
          </div>
          {showPrHeadOption && selectedPr && !isPrHeadSelected && (
            <p className="text-[11px] leading-relaxed text-ink-500">
              This PR&apos;s head branch{" "}
              <span className="font-mono">
                {selectedPr.head_repo
                  ? `${selectedPr.head_repo}:${selectedPr.head}`
                  : selectedPr.head}
              </span>{" "}
              is not on origin (fork).{" "}
              <button
                type="button"
                onClick={usePrHeadBase}
                className="underline-offset-2 hover:text-ink-300 hover:underline"
              >
                Base the worktree on PR #{selectedPr.number} head
              </button>{" "}
              to address its reviews — publish will push back to that PR, or open a new PR if the
              fork disallows edits.
            </p>
          )}
          {isPrHeadSelected && selectedPr && (
            <p className="text-[11px] leading-relaxed text-syrup-300">
              Worktree starts at PR #{selectedPr.number} head; target is its base (
              <span className="font-mono">{selectedPr.base ?? targetBranch}</span>). Publish
              defaults to Push to PR #{selectedPr.number}.
            </p>
          )}
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

      <div className="rounded-xl border border-ink-800/70">
        <button
          type="button"
          onClick={() => setShowAdvanced((v) => !v)}
          aria-expanded={showAdvanced}
          className="flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left"
        >
          <span className="shrink-0 text-xs font-medium text-ink-400">
            Advanced {showAdvanced ? "▾" : "▸"}
          </span>
          <span className="truncate font-mono text-[11px] text-ink-500">{advancedSummary}</span>
        </button>
        {showAdvanced && (
          <div className="space-y-4 px-4 pb-4">
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
              <Select
                label="Backend"
                value={agentCli ?? ""}
                onChange={setAgentCli}
                disabled={settingsLoading}
              >
                {backendOptions.map((c) => (
                  <option key={c} value={c}>
                    {c}
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
          </div>
        )}
      </div>

      <div>
        <div className="mb-1.5 flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <span className="text-xs font-medium text-ink-400">
            Instructions
            {!promptRequired && (
              <span className="font-normal text-ink-500">
                {" "}
                (optional — the reviewer already knows what to do)
              </span>
            )}
          </span>
          {(STARTERS[type] ?? []).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => insertStarter(s)}
              className="rounded-full border border-ink-800 px-2 py-0.5 text-[11px] text-ink-400 transition-colors hover:border-ink-600 hover:text-ink-200"
            >
              {s}
            </button>
          ))}
        </div>
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={4}
          placeholder={
            type === "pr_review"
              ? "Optional focus areas — e.g. security, performance…"
              : type === "issue_fix"
                ? "How should the agent approach the fix? (optional context)"
                : "Instructions…"
          }
          className="field resize-y"
        />
      </div>

      {error && <p className="text-xs text-red-400">{error}</p>}

      <div className="flex justify-end">
        <button
          type="submit"
          disabled={
            busy || settingsLoading || !effectiveRepoId || (promptRequired && !prompt.trim())
          }
          className="btn-primary"
        >
          {busy ? "Creating…" : settingsLoading ? "Loading…" : "Create"}
        </button>
      </div>
    </form>
  );
}

const FILTERS = [
  { id: "all", label: "All", test: (_t: Task) => true },
  {
    id: "needs_you",
    label: "Needs you",
    test: (t: Task) => t.attention === "needs_you",
  },
  {
    id: "running",
    label: "Running",
    test: (t: Task) => t.status === "queued" || t.status === "running",
  },
  { id: "done", label: "Done", test: (t: Task) => t.status === "done" },
  {
    id: "failed",
    label: "Failed",
    test: (t: Task) =>
      t.status === "failed" || t.status === "timed_out" || t.status === "interrupted",
  },
  { id: "review", label: "Review", test: (t: Task) => t.status === "needs_approval" },
  {
    id: "cancelled",
    label: "Cancelled",
    test: (t: Task) => t.status === "cancelled",
  },
] as const;

type FilterId = (typeof FILTERS)[number]["id"];

/** Friendly empty states per filter — "No tasks in failed yet" reads like a bug. */
const EMPTY_STATE: Record<FilterId, string> = {
  all: "No tasks yet — create one above.",
  needs_you: "Nothing needs you. All agents are working or done.",
  running: "Nothing running right now.",
  done: "No finished tasks yet.",
  failed: "No failed tasks — everything's healthy.",
  review: "Nothing awaiting review.",
  cancelled: "No cancelled tasks.",
};

type SortKey = "id" | "updated_at" | "status" | "repo";
const PAGE_SIZE = 10;

function GhLink({ repo, kind, number }: { repo: string; kind: "pull" | "issues"; number: number }) {
  const label = kind === "pull" ? `#${number}` : `issue #${number}`;
  return (
    <a
      className="font-mono text-xs text-syrup-400 hover:text-syrup-300"
      href={`https://github.com/${repo}/${kind}/${number}`}
      target="_blank"
      rel="noreferrer"
    >
      {label}
    </a>
  );
}

export default function Tasks() {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const urlView = searchParams.get("view");
  const fromMission =
    (location.state as { from?: string } | null)?.from === "mission" ||
    searchParams.get("from") === "mission" ||
    searchParams.get("action") === "new";

  const [tasks, setTasks] = useState<Task[]>([]);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<FilterId>("all");
  const [query, setQuery] = useState("");
  const [repoFilter, setRepoFilter] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("updated_at");
  const [sortDesc, setSortDesc] = useState(true);
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [expandedPrompt, setExpandedPrompt] = useState<number | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [flash, setFlash] = useState<number | null>(null);
  const [lastLoaded, setLastLoaded] = useState<Date | null>(null);
  const [prefill, setPrefill] = useState<TaskPrefill | null>(null);
  const [prefillNonce, setPrefillNonce] = useState(0);
  const view: "queue" | "mission" = (() => {
    if (urlView === "mission" || urlView === "queue") return urlView;
    try {
      return localStorage.getItem("jalebi-tasks-view") === "mission" ? "mission" : "queue";
    } catch {
      return "queue";
    }
  })();
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(() => {
    api
      .getTasks()
      .then((t) => {
        setTasks(t);
        setLastLoaded(new Date());
      })
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

  useEffect(
    () => () => {
      if (flashTimer.current) clearTimeout(flashTimer.current);
    },
    []
  );

  const stats = useMemo(() => {
    const running = tasks.filter((t) => t.status === "queued" || t.status === "running").length;
    const done = tasks.filter((t) => t.status === "done").length;
    const review = tasks.filter((t) => t.status === "needs_approval").length;
    const needsYou = tasks.filter((t) => t.attention === "needs_you").length;
    return { total: tasks.length, running, done, review, needsYou };
  }, [tasks]);

  const filterCounts = useMemo(() => {
    const counts = {} as Record<FilterId, number>;
    for (const f of FILTERS) counts[f.id] = tasks.filter(f.test).length;
    return counts;
  }, [tasks]);

  const repoNames = useMemo(() => {
    const names = new Set<string>();
    for (const t of tasks) names.add(t.repo_full_name ?? repoName(repos, t.repo_id));
    return [...names].sort((a, b) => a.localeCompare(b));
  }, [tasks, repos]);

  const visible = useMemo(() => {
    const test = FILTERS.find((f) => f.id === filter)!.test;
    const q = query.trim().toLowerCase();
    const list = tasks.filter((t) => {
      if (!test(t)) return false;
      const rn = t.repo_full_name ?? repoName(repos, t.repo_id);
      if (repoFilter && rn !== repoFilter) return false;
      if (!q) return true;
      return (
        String(t.id).includes(q) ||
        t.prompt.toLowerCase().includes(q) ||
        rn.toLowerCase().includes(q)
      );
    });
    list.sort((a, b) => {
      const av =
        sortKey === "status"
          ? a.status
          : sortKey === "id"
            ? a.id
            : sortKey === "repo"
              ? (a.repo_full_name ?? repoName(repos, a.repo_id))
              : a.updated_at;
      const bv =
        sortKey === "status"
          ? b.status
          : sortKey === "id"
            ? b.id
            : sortKey === "repo"
              ? (b.repo_full_name ?? repoName(repos, b.repo_id))
              : b.updated_at;
      const cmp =
        typeof av === "number" && typeof bv === "number"
          ? av - bv
          : String(av).localeCompare(String(bv));
      return sortDesc ? -cmp : cmp;
    });
    return list;
  }, [tasks, filter, query, repoFilter, sortKey, sortDesc, repos]);

  const pageCount = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  const pageRows = visible.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDesc((d) => !d);
    } else {
      setSortKey(key);
      setSortDesc(key !== "id" && key !== "repo");
    }
    setPage(0);
  }

  function selectFilter(f: FilterId) {
    setFilter(f);
    setPage(0);
    setSelected(new Set());
  }

  function selectView(v: "queue" | "mission") {
    try {
      localStorage.setItem("jalebi-tasks-view", v);
    } catch {
      /* private-mode storage — non-fatal */
    }
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("view", v);
        next.delete("from");
        next.delete("action");
        return next;
      },
      { replace: false }
    );
    try {
      if (typeof window !== "undefined") {
        window.scrollTo({ top: 0, behavior: "instant" as ScrollBehavior });
      }
    } catch {
      /* ignore */
    }
  }

  // Mission-control order buttons flip to the queue, pre-select the
  // task type matching the ordered snack, and push history without scrolling.
  function handleMissionOrder(kind?: SnackKind) {
    const type = kind === "samosa" ? "issue_fix" : kind === "pakora" ? "pr_review" : "freeform";
    setPrefill({ type });
    setPrefillNonce((n) => n + 1);
    try {
      localStorage.setItem("jalebi-tasks-view", "queue");
    } catch {
      /* ignore */
    }
    // Push new history entry with view=queue and from=mission
    // Zero scrolling!
    navigate("/?view=queue&from=mission", { state: { from: "mission" } });
    try {
      if (typeof window !== "undefined") {
        window.scrollTo({ top: 0, behavior: "instant" as ScrollBehavior });
      }
    } catch {
      /* ignore */
    }
  }

  function handleDismissAttention(taskId: number) {
    setTasks((prev) => prev.map((t) => (t.id === taskId ? { ...t, attention: "done" } : t)));
    api.dismissAttention(taskId).catch((e) => {
      setError(e.message);
      load();
    });
  }

  function handleCancel(taskId: number) {
    api
      .cancelTask(taskId)
      .then(() => load())
      .catch((e) => setError(e instanceof Error ? e.message : "failed to cancel task"));
  }

  function handleClone(t: Task) {
    setPrefill({
      repoId: t.repo_id,
      type: t.type,
      prompt: t.prompt,
      sourceBranch: t.source_branch ?? undefined,
      targetBranch: t.target_branch ?? undefined,
      agentId: t.agent_id ?? undefined,
      cli: t.cli ?? undefined,
      model: t.model ?? undefined,
      publishMode: (t.publish_mode as "auto" | "manual" | "") ?? "",
      prNumber:
        t.prs?.[0] != null
          ? String(t.prs[0])
          : t.pr_number != null
            ? String(t.pr_number)
            : undefined,
      issueNumber: t.issues?.[0] != null ? String(t.issues[0]) : undefined,
      patName: t.pat_name ?? undefined,
      envVars: t.env_vars ?? undefined,
    });
    setPrefillNonce((n) => n + 1);
  }

  function handleCreated(id?: number) {
    load();
    if (id === undefined) return;
    setFlash(id);
    if (flashTimer.current) clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setFlash(null), 10000);
  }

  function toggleSelected(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSelectPage(ids: number[]) {
    setSelected((prev) => {
      const next = new Set(prev);
      const allIn = ids.every((id) => next.has(id));
      if (allIn) ids.forEach((id) => next.delete(id));
      else ids.forEach((id) => next.add(id));
      return next;
    });
  }

  async function handleBulkDelete() {
    const ids = [...selected];
    if (ids.length === 0) return;
    if (!window.confirm(`Delete ${ids.length} selected task${ids.length === 1 ? "" : "s"}?`)) {
      return;
    }
    setBulkBusy(true);
    try {
      await Promise.all(ids.map((id) => api.deleteTask(id)));
      setSelected(new Set());
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "bulk delete failed");
      load();
    } finally {
      setBulkBusy(false);
    }
  }

  async function handleBulkDismiss() {
    const ids = [...selected];
    if (ids.length === 0) return;
    setBulkBusy(true);
    try {
      await Promise.all(ids.map((id) => api.dismissAttention(id)));
      setSelected(new Set());
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "bulk dismiss failed");
      load();
    } finally {
      setBulkBusy(false);
    }
  }

  const statCards: { label: string; value: number; accent: string; filter: FilterId }[] = [
    { label: "Total", value: stats.total, accent: "text-ink-100", filter: "all" },
    { label: "Needs you", value: stats.needsYou, accent: "text-syrup-300", filter: "needs_you" },
    { label: "Running", value: stats.running, accent: "text-syrup-300", filter: "running" },
    { label: "Done", value: stats.done, accent: "text-green-300", filter: "done" },
    { label: "Needs review", value: stats.review, accent: "text-purple-300", filter: "review" },
  ];

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3 animate-fade-up">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-ink-100">Tasks</h1>
          <p className="mt-1 text-sm text-ink-500">
            Your agent queue — what&apos;s running, what&apos;s done, what needs a decision.
          </p>
        </div>
        <div
          role="group"
          aria-label="Tasks view"
          className="flex overflow-hidden rounded-full border border-ink-800 text-sm"
        >
          {(["queue", "mission"] as const).map((v) => (
            <button
              key={v}
              type="button"
              onClick={() => selectView(v)}
              aria-pressed={view === v}
              className={`px-3.5 py-1 transition-colors ${
                view === v
                  ? "bg-syrup-500 font-semibold text-ink-950"
                  : "text-ink-400 hover:bg-ink-850 hover:text-ink-100"
              }`}
            >
              {v === "queue" ? "Queue" : "Mission control"}
            </button>
          ))}
        </div>
      </header>

      {view === "mission" ? (
        <BrewHouse
          tasks={tasks}
          repos={repos}
          onNewTask={handleMissionOrder}
          onCancel={handleCancel}
        />
      ) : (
        <>
          <div
            className="grid grid-cols-2 gap-3 sm:grid-cols-5 animate-fade-up"
            style={{ animationDelay: "0.05s" }}
          >
            {statCards.map((c) => (
              <button
                key={c.label}
                type="button"
                onClick={() => selectFilter(c.filter)}
                title={`Show ${c.label.toLowerCase()} tasks`}
                className={`surface px-5 py-4 text-left transition-colors hover:border-ink-600 ${
                  filter === c.filter ? "border-syrup-500/50" : ""
                }`}
              >
                <span className="block text-xs font-medium uppercase tracking-wider text-ink-500">
                  {c.label}
                </span>
                <span
                  className={`mt-1 block font-mono text-3xl font-medium tabular-nums ${c.accent}`}
                >
                  {c.value}
                </span>
              </button>
            ))}
          </div>

          {/* Phase 4 T4.4 — live "running now" panel: one card per
          queued/running task, scroll-bounded for long queues. */}
          {visible.some((t) => t.status === "running" || t.status === "queued") && (
            <div className="max-h-[24rem] space-y-2 overflow-y-auto">
              {visible
                .filter((t) => t.status === "running" || t.status === "queued")
                .map((t) => (
                  <RunningCard
                    key={t.id}
                    task={t}
                    repoName={t.repo_full_name ?? repoName(repos, t.repo_id)}
                    onCancel={() => handleCancel(t.id)}
                  />
                ))}
            </div>
          )}

          {fromMission && (
            <div className="flex items-center justify-between rounded-xl border border-syrup-500/30 bg-syrup-950/20 px-4 py-2.5 text-xs text-syrup-300 animate-fade-up">
              <span className="flex items-center gap-2">
                <span className="h-1.5 w-1.5 rounded-full bg-syrup-400" />
                <span>Ordering from Halwai Shop (Mission Control)</span>
              </span>
              <button
                type="button"
                onClick={() => {
                  selectView("mission");
                  navigate("/?view=mission");
                }}
                className="inline-flex items-center gap-1 font-semibold text-syrup-400 hover:text-syrup-200 transition-colors"
              >
                <span>←</span>
                <span>Back to Mission control</span>
              </button>
            </div>
          )}

          <CreateTask
            key={prefillNonce}
            repos={repos}
            accounts={accounts}
            onCreated={handleCreated}
            prefill={prefill}
          />

          {flash != null && (
            <div className="rounded-xl border border-green-500/30 bg-green-500/10 px-4 py-2.5 text-sm text-green-300 flex flex-wrap items-center justify-between gap-2">
              <span>
                Task{" "}
                <Link to={`/tasks/${flash}`} className="font-mono underline underline-offset-2">
                  #{flash}
                </Link>{" "}
                created — it will pick up a worker shortly.
              </span>
              <div className="flex items-center gap-3 text-xs">
                {fromMission && (
                  <button
                    type="button"
                    onClick={() => {
                      selectView("mission");
                      navigate("/?view=mission");
                    }}
                    className="font-medium text-green-300 hover:text-green-100 underline underline-offset-2"
                  >
                    Back to Mission control →
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setFlash(null)}
                  className="text-xs text-green-400/70 hover:text-green-300"
                >
                  Dismiss
                </button>
              </div>
            </div>
          )}

          {error && (
            <p className="text-sm text-red-400">
              {error}{" "}
              <button type="button" onClick={load} className="underline underline-offset-2">
                Retry
              </button>
            </p>
          )}

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
                  {f.label} ({filterCounts[f.id]})
                </button>
              ))}
              <select
                value={repoFilter}
                onChange={(e) => {
                  setRepoFilter(e.target.value);
                  setPage(0);
                }}
                aria-label="Filter by repository"
                className="field !w-auto !py-1 text-sm"
              >
                <option value="">All repos</option>
                {repoNames.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
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

            <div className="flex flex-wrap items-center gap-2 border-b border-ink-800 px-4 py-2 text-xs text-ink-500">
              {selected.size > 0 ? (
                <>
                  <span className="font-mono">{selected.size} selected</span>
                  <button
                    type="button"
                    onClick={() => void handleBulkDismiss()}
                    disabled={bulkBusy}
                    className="btn-ghost !px-2 !py-1 disabled:opacity-40"
                  >
                    Dismiss attention
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleBulkDelete()}
                    disabled={bulkBusy}
                    className="btn-ghost !px-2 !py-1 text-red-300 disabled:opacity-40"
                  >
                    {bulkBusy ? "Working…" : "Delete"}
                  </button>
                  <button
                    type="button"
                    onClick={() => setSelected(new Set())}
                    className="btn-ghost !px-2 !py-1"
                  >
                    Clear
                  </button>
                </>
              ) : (
                <span>
                  {visible.length} task{visible.length === 1 ? "" : "s"}
                  {lastLoaded ? ` · updated ${timeAgo(lastLoaded.toISOString())}` : ""}
                </span>
              )}
              <span className="ml-auto flex items-center gap-2">
                {pageCount > 1 && (
                  <>
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
                  </>
                )}
                <button type="button" onClick={load} className="btn-ghost !px-2 !py-1">
                  Refresh
                </button>
              </span>
            </div>

            <div className="max-h-[60vh] overflow-y-auto">
              <table className="w-full text-sm">
                <thead className="sticky top-0 z-10 bg-ink-900">
                  <tr className="text-left text-xs uppercase tracking-wider text-ink-500">
                    <th className="px-4 pt-3 pb-2 font-medium">
                      <input
                        type="checkbox"
                        aria-label="Select tasks on this page"
                        checked={pageRows.length > 0 && pageRows.every((t) => selected.has(t.id))}
                        onChange={() => toggleSelectPage(pageRows.map((t) => t.id))}
                        className="accent-syrup-500"
                      />
                    </th>
                    <th
                      className="cursor-pointer select-none px-4 pt-3 pb-2 font-medium hover:text-ink-300"
                      onClick={() => toggleSort("id")}
                    >
                      ID {sortKey === "id" ? (sortDesc ? "↓" : "↑") : ""}
                    </th>
                    <th
                      className="cursor-pointer select-none px-4 pb-2 font-medium hover:text-ink-300"
                      onClick={() => toggleSort("status")}
                    >
                      Status {sortKey === "status" ? (sortDesc ? "↓" : "↑") : ""}
                    </th>
                    <th
                      className="cursor-pointer select-none px-4 pb-2 font-medium hover:text-ink-300"
                      onClick={() => toggleSort("repo")}
                    >
                      Repo {sortKey === "repo" ? (sortDesc ? "↓" : "↑") : ""}
                    </th>
                    <th className="px-4 pb-2 font-medium">Prompt</th>
                    <th className="px-4 pb-2 font-medium">PR / Issues</th>
                    <th
                      className="cursor-pointer select-none px-4 pb-2 font-medium hover:text-ink-300"
                      onClick={() => toggleSort("updated_at")}
                    >
                      Updated {sortKey === "updated_at" ? (sortDesc ? "↓" : "↑") : ""}
                    </th>
                    <th className="px-4 pb-2 font-medium">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {pageRows.length === 0 && (
                    <tr>
                      <td colSpan={8} className="px-4 py-8 text-center text-ink-600">
                        {query || repoFilter ? (
                          <>
                            No tasks{filter !== "all" ? ` in “${filter}”` : ""} matching your
                            search.
                          </>
                        ) : (
                          EMPTY_STATE[filter]
                        )}
                      </td>
                    </tr>
                  )}
                  {pageRows.map((t) => {
                    const rn = t.repo_full_name ?? repoName(repos, t.repo_id);
                    const expanded = expandedPrompt === t.id;
                    const failed =
                      t.status === "failed" ||
                      t.status === "timed_out" ||
                      t.status === "interrupted";
                    return (
                      <tr
                        key={t.id}
                        onClick={() => navigate(`/tasks/${t.id}`)}
                        className="cursor-pointer border-t border-ink-800/70 transition-colors hover:bg-ink-875/50"
                      >
                        <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                          <input
                            type="checkbox"
                            aria-label={`Select task #${t.id}`}
                            checked={selected.has(t.id)}
                            onChange={() => toggleSelected(t.id)}
                            className="accent-syrup-500"
                          />
                        </td>
                        <td className="px-4 py-3">
                          <Link
                            to={`/tasks/${t.id}`}
                            onClick={(e) => e.stopPropagation()}
                            className="font-mono text-syrup-400 hover:text-syrup-300"
                          >
                            #{t.id}
                          </Link>
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <StatusBadge status={t.status} />
                            {t.attention === "needs_you" && (
                              <AttentionBadge attention={t.attention} />
                            )}
                            {/* DepBadges renders Links — stop them bubbling to the row nav. */}
                            <span onClick={(e) => e.stopPropagation()}>
                              <DepBadges
                                dependsOn={t.depends_on}
                                blockedBy={t.blocked_by}
                                blocking={t.blocking}
                                blocked={t.blocked}
                              />
                            </span>
                            {t.attention === "needs_you" && (
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.preventDefault();
                                  e.stopPropagation();
                                  handleDismissAttention(t.id);
                                }}
                                className="rounded px-1.5 py-0.5 text-[10px] font-medium text-ink-400 ring-1 ring-ink-700/60 hover:bg-ink-800 hover:text-ink-200 transition-colors"
                                title="Dismiss attention for this task"
                              >
                                Dismiss
                              </button>
                            )}
                          </div>
                        </td>
                        <td className="px-4 py-3">
                          <RepoChip name={rn} />
                        </td>
                        <td
                          className={`max-w-xs cursor-pointer px-4 py-3 text-ink-300 ${expanded ? "" : "truncate"}`}
                          title={expanded ? "Collapse" : t.prompt}
                          role="button"
                          tabIndex={0}
                          aria-expanded={expanded}
                          aria-label={expanded ? "Collapse prompt" : "Expand prompt"}
                          onClick={(e) => {
                            e.stopPropagation();
                            setExpandedPrompt(expanded ? null : t.id);
                          }}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              e.stopPropagation();
                              setExpandedPrompt(expanded ? null : t.id);
                            }
                          }}
                        >
                          {t.prompt}
                        </td>
                        <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
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
                        <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                          <div className="flex items-center gap-1">
                            <button
                              type="button"
                              onClick={() => handleClone(t)}
                              title="Clone — pre-fill the form from this task"
                              className="rounded px-1.5 py-0.5 font-mono text-xs text-ink-400 hover:bg-ink-800 hover:text-ink-200"
                            >
                              ⧉
                            </button>
                            {failed && (
                              <button
                                type="button"
                                onClick={() => {
                                  api
                                    .rerunTask(t.id)
                                    .then(() => load())
                                    .catch((e) =>
                                      setError(
                                        e instanceof Error ? e.message : "failed to re-run task"
                                      )
                                    );
                                }}
                                title="Re-run this task"
                                className="rounded px-1.5 py-0.5 font-mono text-xs text-ink-400 hover:bg-ink-800 hover:text-ink-200"
                              >
                                ↻
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

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
        </>
      )}
    </div>
  );
}
