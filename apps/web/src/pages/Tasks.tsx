import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { AttentionBadge } from "../components/AttentionBadge";
import { BrewHouse } from "../components/brew/BrewHouse";
import type { SnackKind } from "../components/brew/snacks";
import { OpsDeck } from "../components/ops/OpsDeck";
import type { JobKind } from "../components/ops/jobStyle";
import { getMissionTheme, setMissionTheme, type MissionTheme } from "../lib/missionTheme";
import { DepBadges } from "../components/DepBadges";
import { EmptyState } from "../components/EmptyState";
import { OnboardingChecklist } from "../components/OnboardingChecklist";
import { RunningCard } from "../components/RunningCard";
import SearchableSelect from "../components/SearchableSelect";
import { StatusBadge } from "../components/StatusBadge";
import { useBackends } from "../hooks/useBackends";
import { GLOSSARY } from "../lib/glossary";
import { groupFpsByScreen } from "../lib/screeningDealt";
import { useStatusAnnouncer } from "../lib/useStatusAnnouncer";
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

/** Cross-page handoff from Screenings: prefill the New-task form. `dealtFps`
 * are finding fingerprints marked dealt only after the create POST succeeds
 * (see handleCreated) — never at navigation time, so abandoning the form
 * leaves the inbox untouched. */
export interface ScreeningHandoff {
  prefill: TaskPrefill;
  dealtFps?: string[];
  screeningHandoffId?: string;
}

function sanitizePrefillType(type: string | undefined): string | undefined {
  if (!type) return type;
  return TASK_TYPES.some((t) => t.value === type) ? type : "freeform";
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
  addressReviews?: boolean;
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
  isFirstTask = false,
}: {
  repos: Repo[];
  accounts: Account[];
  onCreated: (id?: number) => void;
  prefill: TaskPrefill | null;
  isFirstTask?: boolean;
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
  // Creation-time "address the review comments on the linked PR" (freeform
  // only). Defaults to checked as soon as a PR is linked unless the user (or
  // a prefill) explicitly chose otherwise.
  const [addressReviews, setAddressReviews] = useState(prefill?.addressReviews ?? false);
  const [addressTouched, setAddressTouched] = useState(prefill?.addressReviews !== undefined);
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
  const isReview = type === "pr_review";
  // The backend default when the picker is left blank: issue_fix auto-publishes,
  // everything else is manual. Derive the summary and the safety line from this
  // same value so they can never contradict each other.
  const effectivePublish: "auto" | "manual" =
    publishMode === "auto" || publishMode === "manual"
      ? publishMode
      : type === "issue_fix"
        ? "auto"
        : "manual";
  const isAutoPublish = !isReview && effectivePublish === "auto";

  const agentName = agents.find((a) => a.id === agentId)?.name ?? null;
  const advancedSummary = [
    agentCli ?? "…",
    model || "default model",
    agentName ?? "default agent",
    isReview ? "no publish (review)" : `${effectivePublish} publish`,
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
    // Recommended default: linking a PR opts into addressing its review
    // comments — unless the user already toggled the checkbox explicitly.
    if (!addressTouched) setAddressReviews(!!num);
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
          // Prefilled PR link (e.g. the Address-reviewers handoff, which
          // carries no explicit branches): base the work on the PR exactly
          // like a manual pick would — selectPr only runs on dropdown change.
          // Same origin-only rule (fork heads keep the default + hint).
          if (type === "freeform" && prNumber) {
            const pr = c.prs.find((p) => p.number === Number(prNumber)) ?? null;
            if (pr) {
              if (pr.head && c.branches.includes(pr.head)) setSourceBranch(pr.head);
              if (pr.base && c.branches.includes(pr.base)) setTargetBranch(pr.base);
            }
          }
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
        address_reviews: type === "freeform" && prNumber ? addressReviews : undefined,
        // Reviews never publish: omit any stale mode (e.g. carried in from a
        // clone or saved defaults) so the backend never persists a contradictory
        // publish_mode on a pr_review task.
        publish_mode: isReview || publishMode === "" ? undefined : publishMode,
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
      setAddressReviews(false);
      setAddressTouched(false);
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
      <div
        id="new-task"
        tabIndex={-1}
        className="surface flex flex-col items-start gap-3 p-6 animate-fade-up outline-none"
      >
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
      id="new-task"
      tabIndex={-1}
      className="surface space-y-4 p-6 animate-fade-up outline-none"
      style={{ animationDelay: "0.05s" }}
    >
      <div className="flex items-baseline justify-between">
        <h2 className="panel-title">New task</h2>
        <span className="eyebrow">
          {agentCli === null ? "Loading defaults…" : `${agentCli} · runs in a local worktree`}
        </span>
      </div>

      <p className="text-xs text-ink-500">
        {type === "pr_review"
          ? "The agent reviews this pull request in a read-only copy and posts its comments. It never changes the code or opens a merge."
          : "The agent works in a private local copy of the repo on its own branch and cannot push. Jalebi publishes the result for you, and merging is always your decision."}
      </p>

      {isFirstTask && (
        <p className="text-xs text-ink-400">
          New here? Start with a <span className="font-mono">freeform</span> task — describe
          what you want in plain words; the agent figures out the rest.
        </p>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <SearchableSelect
          label="Repository"
          value={effectiveRepoId}
          onChange={(v) => selectRepo(Number(v))}
          options={repos.map((r) => ({
            value: String(r.id),
            label: `${accountLabel(r.pat_name)} / ${r.full_name}`,
          }))}
        />
        <SearchableSelect
          label="Task type"
          value={type}
          onChange={(v) => {
            const next = v as typeof type;
            setType(next);
            // Reviews never publish, so drop any publish pin carried over from
            // another type — the summary and submit must not report a mode.
            if (next === "pr_review") setPublishMode("");
          }}
          options={TASK_TYPES}
        />
      </div>

      {(type === "issue_fix" || type === "pr_review" || type === "freeform") && context && (
        <div className="grid gap-4 sm:grid-cols-2">
          {type === "issue_fix" && (
            <SearchableSelect
              label="Issue"
              value={issueNumber}
              onChange={setIssueNumber}
              placeholder={context.issues.length ? "Select an issue…" : "No open issues"}
              options={context.issues.map((i) => ({
                value: String(i.number),
                label: `#${i.number} — ${i.title}`,
              }))}
            />
          )}
          {type !== "issue_fix" && (
            <SearchableSelect
              label={type === "pr_review" ? "Pull request" : "Link PR (optional)"}
              value={prNumber}
              onChange={selectPr}
              placeholder={context.prs.length ? "Select a PR…" : "No open PRs"}
              options={context.prs.map((p) => ({
                value: String(p.number),
                label: `#${p.number} — ${p.title}`,
              }))}
            />
          )}
        </div>
      )}
      {type === "freeform" && prNumber && (
        <label className="flex cursor-pointer items-start gap-2 text-xs text-ink-400">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={addressReviews}
            onChange={(e) => {
              setAddressReviews(e.target.checked);
              setAddressTouched(true);
            }}
          />
          <span>
            Address the review comments on this PR — the agent fixes what reviewers said (current
            comments are embedded; newer ones are fetched live).
          </span>
        </label>
      )}

      {type === "issue_fix" ? (
        <div className="grid gap-4 sm:grid-cols-2">
          <SearchableSelect
            label="Target branch (worktree base / PR base)"
            value={targetBranch}
            onChange={setTargetBranch}
            placeholder={
              context ? (context.branches.length ? "default" : "no branches") : "loading…"
            }
            options={context?.branches ?? []}
          />
        </div>
      ) : type === "pr_review" ? null : (
        <div className="space-y-3">
          <div className="grid gap-4 sm:grid-cols-2">
            <SearchableSelect
              label="Source branch"
              value={sourceBranch}
              onChange={setSourceBranch}
              placeholder={
                context ? (context.branches.length ? "default" : "no branches") : "loading…"
              }
              options={[
                ...(context?.branches ?? []),
                ...(showPrHeadOption || isPrHeadSelected
                  ? selectedPr
                    ? [
                        {
                          value: prHeadValue,
                          label:
                            `PR #${selectedPr.number} head` +
                            (selectedPr.head_repo
                              ? ` (${selectedPr.head_repo}:${selectedPr.head})`
                              : ` (${selectedPr.head})`),
                        },
                      ]
                    : []
                  : []),
              ]}
            />
            <SearchableSelect
              label="Target branch (PR base)"
              value={targetBranch}
              onChange={setTargetBranch}
              placeholder={
                context ? (context.branches.length ? "default" : "no branches") : "loading…"
              }
              options={context?.branches ?? []}
            />
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
            Advanced options {showAdvanced ? "▾" : "▸"}
          </span>
          <span className="truncate font-mono text-[11px] text-ink-500">{advancedSummary}</span>
        </button>
        {showAdvanced && (
          <div className="space-y-4 px-4 pb-4">
            <div className="grid gap-4 sm:grid-cols-4">
              <SearchableSelect
                label="Agent"
                value={agentId}
                onChange={setAgentId}
                placeholder="Default build agent"
                options={agents.map((a) => ({ value: a.id, label: `${a.name} (${a.id})` }))}
              />
              <SearchableSelect
                label="Backend"
                labelTitle={GLOSSARY.backend}
                value={agentCli ?? ""}
                onChange={(v) => {
                  setAgentCli(v);
                  // A new backend means a new model list — drop the old pick
                  // so a stale id from another backend is never submitted.
                  setModel("");
                }}
                disabled={settingsLoading}
                options={backendOptions}
              />
              <SearchableSelect
                label="Model"
                value={model}
                onChange={setModel}
                placeholder="default model"
                options={models}
                allowCustom
                staleHint="Not in this backend's known list — will be sent as-is."
              />
              <SearchableSelect
                label="Credentials"
                value={patName}
                onChange={setPatName}
                placeholder="Inherit repo account"
                options={accounts.map((a) => ({
                  value: a.name,
                  label: `${a.login ?? a.name} (${a.masked})`,
                }))}
              />
              {type === "pr_review" ? (
                <p className="text-[11px] text-ink-500">
                  Review tasks do not publish a pull request — the review is posted as comments on
                  the PR.
                </p>
              ) : (
                <div>
                  <SearchableSelect
                    label="Publish mode"
                    value={publishMode}
                    onChange={(v) => setPublishMode(v as "auto" | "manual" | "")}
                    placeholder="Auto (by type)"
                    options={[
                      { value: "auto", label: "Auto — publish when done" },
                      { value: "manual", label: "Manual — I publish" },
                    ]}
                  />
                  <p className="mt-1.5 text-[11px] text-ink-500">
                    Auto publishes when the task finishes, Manual waits for you to review and click
                    Publish.
                  </p>
                </div>
              )}
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

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-xs text-ink-500">
          {type === "pr_review"
            ? "Review tasks only post comments — nothing is pushed or published. You can cancel while it runs."
            : `The task can be cancelled while it runs. ${
                isAutoPublish
                  ? "A pull request will be published automatically when the task finishes."
                  : "Publishing a pull request is a separate step you control."
              }`}
        </p>
        <button
          type="submit"
          disabled={
            busy || settingsLoading || !effectiveRepoId || (promptRequired && !prompt.trim())
          }
          className="btn-primary shrink-0 self-end sm:self-auto"
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
  const statusAnnouncement = useStatusAnnouncer(tasks);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  // Loaded for the onboarding checklist's backend step (null = still loading).
  const [settings, setSettings] = useState<SettingsMap | null>(null);
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
  // Finding fingerprints carried by a screening handoff — marked dealt only
  // after the new task's create POST succeeds (never at navigation time, so
  // abandoning the form leaves the inbox untouched).
  const [pendingDealtFps, setPendingDealtFps] = useState<string[]>([]);
  // One-shot ids of consumed screening handoffs (guards re-apply on
  // re-render; the state entry itself is cleared with a replace navigation).
  const consumedHandoffs = useRef<Set<string>>(new Set());
  const view: "queue" | "mission" = (() => {
    if (urlView === "mission" || urlView === "queue") return urlView;
    try {
      return localStorage.getItem("jalebi-tasks-view") === "mission" ? "mission" : "queue";
    } catch {
      return "queue";
    }
  })();

  const [missionTheme, setMissionThemeState] = useState<MissionTheme>(() => getMissionTheme());

  const handleMissionThemeChange = useCallback((nextTheme: MissionTheme) => {
    setMissionTheme(nextTheme);
    setMissionThemeState(nextTheme);
  }, []);
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
    api
      .getSettings()
      .then(setSettings)
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

  // Screening → New-task handoff (one-shot). Applies even while Tasks stays
  // mounted: setPrefill + nonce remount re-seeds the form (same mechanism as
  // Clone/Mission-order). The entry is cleared with a replace navigation so
  // refresh/back never re-injects a stale prompt; unrelated state keys (e.g.
  // `from`) survive. A handoff id guards StrictMode double-effects.
  useEffect(() => {
    const raw = location.state as (Partial<ScreeningHandoff> & Record<string, unknown>) | null;
    if (raw == null || typeof raw !== "object" || raw.prefill == null) return;
    const pf = raw.prefill as TaskPrefill;
    if (typeof pf !== "object" || (pf.prompt != null && typeof pf.prompt !== "string")) return;
    const id = typeof raw.screeningHandoffId === "string" ? raw.screeningHandoffId : null;
    if (id !== null && consumedHandoffs.current.has(id)) return;
    if (id !== null) consumedHandoffs.current.add(id);
    const fps = Array.isArray(raw.dealtFps)
      ? raw.dealtFps.filter((x) => typeof x === "string")
      : [];
    setPrefill({ ...pf, type: sanitizePrefillType(pf.type) });
    setPrefillNonce((n) => n + 1);
    setPendingDealtFps(fps);
    // The form lives in the queue view only (mission renders BrewHouse
    // instead) — flip to it, same as a mission-control order does. Replace
    // keeps Back pointed at Screenings instead of a stale prefill entry.
    try {
      localStorage.setItem("jalebi-tasks-view", "queue");
    } catch {
      /* private-mode storage — non-fatal */
    }
    const { prefill: _dropP, dealtFps: _dropD, screeningHandoffId: _dropI, ...rest } = raw;
    navigate("/?view=queue", {
      replace: true,
      state: Object.keys(rest).length > 0 ? rest : null,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.state]);

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
  // task type matching the ordered snack or job kind, and push history without scrolling.
  function handleMissionOrder(kind?: SnackKind | JobKind) {
    const type =
      kind === "samosa" || kind === "fix"
        ? "issue_fix"
        : kind === "pakora" || kind === "review"
          ? "pr_review"
          : "freeform";
    setPrefill({ type });
    setPendingDealtFps([]);
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
    // Persisted types outside the manual dropdown (screen_finding,
    // triggered) fall back to freeform so the type select stays valid.
    setPrefill({
      repoId: t.repo_id,
      type: sanitizePrefillType(t.type) ?? "freeform",
      prompt: t.prompt,
      sourceBranch: t.source_branch ?? undefined,
      targetBranch: t.target_branch ?? undefined,
      agentId: t.agent_id ?? undefined,
      cli: t.cli ?? undefined,
      model: t.model ?? undefined,
      publishMode: (t.publish_mode as "auto" | "manual" | "") ?? "",
      addressReviews: t.address_reviews ?? undefined,
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
    // A clone replaces any screening handoff in the form.
    setPendingDealtFps([]);
  }

  function handleCreated(id?: number) {
    load();
    setError(null);
    if (id === undefined) return;
    // The screening handoff's findings become dealt only now — the create
    // POST succeeded. Best-effort: a mark failure never blocks the task;
    // the error tells the owner to mark from Screenings instead.
    if (pendingDealtFps.length > 0) {
      const fps = pendingDealtFps;
      setPendingDealtFps([]);
      const byScreen = groupFpsByScreen(fps);
      Promise.all([...byScreen].map(([sid, list]) => api.markDealt(sid, list))).catch(() => {
        setError(
          "Task created, but its findings could not be marked dealt — mark them from Screenings."
        );
      });
    }
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

  const handleStartTask = useCallback(() => {
    const el = document.getElementById("new-task");
    if (el) {
      el.scrollIntoView({ behavior: "smooth" });
      el.focus();
    }
  }, []);

  const reassuranceText = useMemo(() => {
    if (tasks.length === 0) return "No tasks yet, nothing is running";
    const running = tasks.filter((t) => t.status === "running").length;
    const queued = tasks.filter((t) => t.status === "queued").length;
    const needsYou = tasks.filter((t) => t.attention === "needs_you").length;
    return `${running} running, ${queued} queued, ${needsYou} needs you`;
  }, [tasks]);

  const statCards: { label: string; value: number; accent: string; filter: FilterId }[] = [
    { label: "Total", value: stats.total, accent: "text-ink-100", filter: "all" },
    { label: "Needs you", value: stats.needsYou, accent: "text-syrup-300", filter: "needs_you" },
    { label: "Running", value: stats.running, accent: "text-syrup-300", filter: "running" },
    { label: "Done", value: stats.done, accent: "text-green-300", filter: "done" },
    { label: "Needs review", value: stats.review, accent: "text-purple-300", filter: "review" },
  ];

  return (
    <div className="space-y-6">
      <div className="sr-only" role="status" aria-live="polite">
        {statusAnnouncement}
      </div>
      <header className="flex flex-wrap items-start justify-between gap-3 animate-fade-up">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-ink-100">Tasks</h1>
          <p className="mt-1 text-sm text-ink-500">
            Your agent queue — what&apos;s running, what&apos;s done, what needs a decision.
          </p>
          {view === "queue" && (
            <p className="mt-1 text-xs text-ink-500">
              {reassuranceText}
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {view === "mission" && (
            <div
              role="group"
              aria-label="Mission theme"
              className="flex overflow-hidden rounded-full border border-ink-800 text-xs"
            >
              {(["ops", "brew"] as const).map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => handleMissionThemeChange(t)}
                  aria-pressed={missionTheme === t}
                  className={`px-3 py-1 transition-colors cursor-pointer ${
                    missionTheme === t
                      ? "bg-ink-800 font-semibold text-ink-100"
                      : "text-ink-400 hover:bg-ink-850 hover:text-ink-200"
                  }`}
                >
                  {t === "ops" ? "Ops Deck" : "Halwai"}
                </button>
              ))}
            </div>
          )}

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
        </div>
      </header>

      {view === "mission" ? (
        missionTheme === "ops" ? (
          <OpsDeck
            tasks={tasks}
            repos={repos}
            onNewTask={handleMissionOrder}
            onCancel={handleCancel}
          />
        ) : (
          <BrewHouse
            tasks={tasks}
            repos={repos}
            onNewTask={handleMissionOrder}
            onCancel={handleCancel}
          />
        )
      ) : (
        <>
          <OnboardingChecklist
            accounts={accounts.length}
            repos={repos.length}
            tasks={tasks.length}
            hasModelChoice={(settings?.default_model ?? "").trim() !== ""}
            hasPublishedPr={tasks.some((t) => t.pr_number != null)}
            onStartTask={handleStartTask}
          />

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
                <span>Ordering from Mission Control</span>
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
            isFirstTask={tasks.length === 0 && lastLoaded !== null}
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
              <SearchableSelect
                label="Filter by repository"
                value={repoFilter}
                onChange={(v) => {
                  setRepoFilter(v);
                  setPage(0);
                }}
                placeholder="All repos"
                options={repoNames}
              />
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
                      <td colSpan={8} className="p-6">
                        {query || repoFilter ? (
                          <div className="py-4 text-center text-sm text-ink-500">
                            No tasks{filter !== "all" ? ` in “${filter}”` : ""} matching your
                            search.
                          </div>
                        ) : filter === "all" && tasks.length === 0 ? (
                          <EmptyState
                            icon="📋"
                            title="No tasks yet"
                            description={
                              repos.length === 0
                                ? "Create your first task above, or connect a repository first."
                                : "Create your first task above to start an agent run."
                            }
                            action={
                              repos.length === 0 ? (
                                <Link to="/repos" className="btn-primary">
                                  Connect repository
                                </Link>
                              ) : undefined
                            }
                          />
                        ) : (
                          <div className="py-4 text-center text-sm text-ink-500">
                            {EMPTY_STATE[filter]}
                          </div>
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
                            <DepBadges
                              dependsOn={t.depends_on}
                              blockedBy={t.blocked_by}
                              blocking={t.blocking}
                              blocked={t.blocked}
                            />
                            {t.attention === "needs_you" && (
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.preventDefault();
                                  e.stopPropagation();
                                  handleDismissAttention(t.id);
                                }}
                                className="inline-flex items-center min-h-6 rounded px-1.5 py-0.5 text-[10px] font-medium text-ink-400 ring-1 ring-ink-700/60 hover:bg-ink-800 hover:text-ink-200 transition-colors"
                                title={`Dismiss attention for this task. ${GLOSSARY["dismiss-attention"]}`}
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
                              <span className="text-ink-500">–</span>
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
                              className="inline-flex items-center justify-center min-h-6 min-w-6 rounded px-1.5 py-0.5 font-mono text-xs text-ink-400 hover:bg-ink-800 hover:text-ink-200"
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
                                className="inline-flex items-center justify-center min-h-6 min-w-6 rounded px-1.5 py-0.5 font-mono text-xs text-ink-400 hover:bg-ink-800 hover:text-ink-200"
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
