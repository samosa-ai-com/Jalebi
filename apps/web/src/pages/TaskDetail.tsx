import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, taskEvents } from "../api/client";
import { StatusBadge } from "../components/StatusBadge";
import { AttentionBadge } from "../components/AttentionBadge";
import FileBrowser from "../components/FileBrowser";
import Markdown from "../components/Markdown";
import { MergeReadinessPanel } from "../components/MergeReadinessPanel";
import WaitingCard from "../components/WaitingCard";
import PublishDialog from "../components/PublishDialog";
import { parseUnifiedDiff } from "../lib/unifiedDiff";
import { useInView } from "../lib/useInView";
import type { Account, Artifact, CatalogAgent, Followup, GithubPr, Repo, Run, SseEvent, Task } from "../types";

const TERMINAL = new Set(["done", "failed", "timed_out", "cancelled", "needs_approval", "interrupted"]);

const MAX_LIVE = 500; // live timeline buffer cap (backend persists last 500 steps)

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

const TEXT_EXTENSIONS = new Set([
  "txt", "md", "json", "log", "py", "js", "ts", "tsx", "jsx", "sh", "yaml", "yml",
  "toml", "csv", "html", "css", "go", "rs", "java", "c", "h", "cpp", "hpp",
]);
const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "ico", "bmp"]);

// Phase 4 T5.3 — step-type colors (background + text), aligned with the
// StatusBadge dot pattern so the timeline visually pulls from the same palette.
const STEP_STYLE: Record<string, string> = {
  step: "bg-ink-700/30 text-ink-300",
  message: "bg-sky-500/10 text-sky-300",
  tool_call: "bg-chai-500/10 text-chai-300",
  diff: "bg-green-500/10 text-green-300",
  done: "bg-green-500/10 text-green-300",
  error: "bg-red-500/10 text-red-300",
};

function Action({ onClick, children, disabled }: { onClick: () => void; children: string; disabled?: boolean }) {
  return (
    <button onClick={onClick} disabled={disabled} className="btn-ghost disabled:opacity-40">
      {children}
    </button>
  );
}

function canManualPublish(task: Task | null): boolean {
  if (!task) return false;
  // Show the new Publish button (with the three-mode picker) for any task in
  // `done` — that's where freeform/screen_finding/triggered tasks land when
  // auto-publish is off, and where issue_fix tasks land after auto-publish
  // (showing the button here is harmless: the backend's no-op gate rejects
  // "nothing to publish" with 409). The legacy Publish button for
  // `needs_approval` has its own direct-click path and is rendered separately.
  return task.status === "done";
}

function PublishButton({
  task,
  disabled,
  onPick,
}: {
  task: Task;
  disabled: boolean;
  onPick: (opts: { mode: "new_pr" | "update_pr" | "push_branch"; branch?: string; pr_number?: number }) => void;
}) {
  const hasLinkedPr = task.prs && task.prs.length > 0;
  const defaultPr = hasLinkedPr ? task.prs[0] : undefined;
  const [showAdvanced, setShowAdvanced] = useState(false);
  // Explicit mode choice in the Advanced panel (null = not chosen → smart default).
  const [advancedModeChoice, setAdvancedModeChoice] = useState<"new_pr" | "update_pr" | "push_branch" | null>(null);
  // Explicit pick in the update_pr dropdown ("" = not picked → smart default).
  const [pickedPr, setPickedPr] = useState<number | "">("");
  const [branchInput, setBranchInput] = useState<string>("");
  // The repo's open PRs, fetched so the update_pr picker is not limited to PRs
  // that happened to be linked at task creation (null = still loading).
  const [openPrs, setOpenPrs] = useState<GithubPr[] | null>(null);
  const [prsLoadFailed, setPrsLoadFailed] = useState(false);
  const prsLoadedRef = useRef(false);

  useEffect(() => {
    if (prsLoadedRef.current) return;
    const repo = task.repo_full_name;
    const account = task.pat_name ?? undefined;
    prsLoadedRef.current = true;
    let cancelled = false;
    // Without a resolvable account there is nothing to fetch — resolve empty so
    // the picker just shows linked PRs (state updates only in async callbacks).
    const load: Promise<GithubPr[]> =
      repo && account
        ? api.getGithubContext(repo, account).then((ctx) => ctx.prs ?? [])
        : Promise.resolve([]);
    load
      .then((prs) => {
        if (!cancelled) setOpenPrs(prs);
      })
      .catch(() => {
        if (!cancelled) {
          setOpenPrs([]);
          setPrsLoadFailed(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [task.repo_full_name, task.pat_name]);

  const linkedNumbers = new Set(task.prs ?? []);
  // Smart default: if a PR was attached at creation time, land the work on it.
  // Otherwise, fall back to the first open PR whose head is the branch this
  // task builds on (e.g. a review-fix task working from the PR's head), so the
  // primary action updates that PR instead of silently opening a new one.
  const matchingOpenPr = (openPrs ?? []).find(
    (p) => p.head === task.source_branch && !linkedNumbers.has(p.number)
  );
  const effectiveDefaultPr = defaultPr ?? matchingOpenPr?.number;
  const defaultMode: "new_pr" | "update_pr" = effectiveDefaultPr !== undefined ? "update_pr" : "new_pr";
  const advancedMode: "new_pr" | "update_pr" | "push_branch" =
    advancedModeChoice ?? (defaultMode === "update_pr" ? "update_pr" : "new_pr");
  const advancedPr: number | undefined = pickedPr === "" ? effectiveDefaultPr : pickedPr;

  const primaryLabel = defaultMode === "update_pr" && effectiveDefaultPr !== undefined
    ? `Push to PR #${effectiveDefaultPr}`
    : "Publish";

  // Selectable PRs: linked PRs first (labeled by number), then the repo's open
  // PRs with title + head→base so the owner can pick any PR to update.
  const prOptions: { number: number; label: string }[] = [];
  for (const n of task.prs ?? []) prOptions.push({ number: n, label: `PR #${n}` });
  for (const p of openPrs ?? []) {
    if (linkedNumbers.has(p.number)) continue;
    prOptions.push({ number: p.number, label: `#${p.number} — ${p.title} (${p.head} → ${p.base})` });
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <Action
          onClick={() => {
            if (defaultMode === "update_pr" && effectiveDefaultPr !== undefined) {
              onPick({ mode: "update_pr", pr_number: effectiveDefaultPr });
            } else {
              onPick({ mode: "new_pr" });
            }
          }}
          disabled={disabled}
        >
          {primaryLabel}
        </Action>
        <button
          type="button"
          onClick={() => setShowAdvanced((v) => !v)}
          className="text-xs text-ink-500 underline-offset-2 hover:text-ink-300 hover:underline"
          aria-expanded={showAdvanced}
        >
          {showAdvanced ? "Hide advanced" : "Advanced"}
        </button>
      </div>
      {showAdvanced && (
        <div className="surface-muted space-y-2 rounded-lg p-3 text-xs">
          <div className="flex flex-col gap-1">
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name={`publish-mode-${task.id}`}
                checked={advancedMode === "new_pr"}
                onChange={() => setAdvancedModeChoice("new_pr")}
              />
              <span>Open a new PR (push <span className="font-mono">jalebi/{task.id}</span> → target)</span>
            </label>
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name={`publish-mode-${task.id}`}
                checked={advancedMode === "update_pr"}
                onChange={() => setAdvancedModeChoice("update_pr")}
              />
              <span>Update existing PR (push to its head branch)</span>
            </label>
            {advancedMode === "update_pr" && (
              <select
                aria-label="Pull request to update"
                className="select ml-6 w-fit"
                value={advancedPr ?? ""}
                onChange={(e) => setPickedPr(e.target.value === "" ? "" : Number(e.target.value))}
              >
                <option value="">— pick a PR —</option>
                {prOptions.map((o) => (
                  <option key={o.number} value={o.number}>
                    {o.label}
                  </option>
                ))}
                {openPrs === null && prOptions.length === 0 && (
                  <option value="" disabled>loading PRs…</option>
                )}
                {prsLoadFailed && (
                  <option value="" disabled>couldn't load PRs</option>
                )}
                {openPrs !== null && !prsLoadFailed && prOptions.length === 0 && (
                  <option value="" disabled>no open PRs in this repo</option>
                )}
              </select>
            )}
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name={`publish-mode-${task.id}`}
                checked={advancedMode === "push_branch"}
                onChange={() => setAdvancedModeChoice("push_branch")}
              />
              <span>Push to specific branch (no PR)</span>
            </label>
            {advancedMode === "push_branch" && (
              <input
                type="text"
                className="input ml-6 w-fit"
                placeholder="branch name"
                value={branchInput}
                onChange={(e) => setBranchInput(e.target.value)}
              />
            )}
          </div>
          <div className="flex justify-end">
            <Action
              disabled={
                disabled ||
                (advancedMode === "update_pr" && advancedPr === undefined) ||
                (advancedMode === "push_branch" && !branchInput.trim())
              }
              onClick={() => {
                if (advancedMode === "new_pr") {
                  onPick({ mode: "new_pr" });
                } else if (advancedMode === "update_pr") {
                  onPick({ mode: "update_pr", pr_number: advancedPr });
                } else {
                  onPick({ mode: "push_branch", branch: branchInput.trim() });
                }
              }}
            >
              Run
            </Action>
          </div>
        </div>
      )}
    </div>
  );
}

function ToolCallEntry({ step }: { step: SseEvent }) {
  let title = step.text ?? "";
  let details = step.text ?? "";
  try {
    const data = JSON.parse(step.text ?? "{}");
    const tool = data.tool ?? "";
    title = data.title ? `${tool} — ${data.title}` : (tool || (step.text ?? ""));
    details = JSON.stringify(
      { tool: data.tool, input: data.input, output: data.output, status: data.status },
      null,
      2
    );
  } catch {
    // keep raw text as both title and details
  }
  return (
    <details className="group">
      <summary className="cursor-pointer select-none font-mono text-xs text-chai-300 hover:text-chai-200">
        <span className="mr-1 inline-block transition-transform group-open:rotate-90">▸</span>
        <span className="opacity-70">⚙</span> {title || "tool call"}
      </summary>
      <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap rounded bg-ink-900/60 p-2 font-mono text-[11px] leading-relaxed text-ink-300">
        {details}
      </pre>
    </details>
  );
}

function TimelineItem({ step, index }: { step: SseEvent; index: number }) {
  const dot = STEP_DOT[step.type] ?? "bg-ink-600";
  return (
    <li
      className="relative flex gap-3 animate-fade-up before:absolute before:left-[3.5px] before:top-[9px] before:bottom-[-12px] before:w-px before:bg-ink-800/70 before:content-[''] last:before:hidden"
      style={{ animationDelay: `${Math.min(index * 0.02, 0.3)}s` }}
    >
      <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ring-2 ring-ink-950 ${dot}`} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[11px] text-ink-600">
            {step.ts?.slice(11, 19) ?? ""}
          </span>
          <span
            className={`rounded px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide ${
              STEP_STYLE[step.type] ?? "bg-ink-700/30 text-ink-300"
            }`}
          >
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
        {step.type === "tool_call" ? (
          <div className="mt-0.5">
            <ToolCallEntry step={step} />
          </div>
        ) : step.type === "message" && step.text ? (
          <Markdown className="mt-0.5">{step.text}</Markdown>
        ) : (
          step.text && <p className="mt-0.5 text-sm leading-snug text-ink-300">{step.text}</p>
        )}
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

function runDuration(startedAt: string | null, finishedAt: string | null): string {
  if (!startedAt) return "—";
  const start = new Date(startedAt).getTime();
  const end = finishedAt ? new Date(finishedAt).getTime() : Date.now();
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return "—";
  const totalSec = Math.round((end - start) / 1000);
  if (totalSec < 60) return `${totalSec}s`;
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return `${min}m ${sec}s`;
}

function formatRunTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

function extOf(path: string): string {
  const i = path.lastIndexOf(".");
  return i >= 0 ? path.slice(i + 1).toLowerCase() : "";
}

function FollowUpComposer({
  task,
  followups,
  accounts,
  onSent,
  prefill,
}: {
  task: Task;
  followups: Followup[];
  accounts: Account[];
  onSent: () => void;
  prefill?: { nonce: number; text: string } | null;
}) {
  const [text, setText] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [patName, setPatName] = useState("");
  const [model, setModel] = useState("");
  const [cli, setCli] = useState(task.cli ?? "");
  const [models, setModels] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [defaultBackend, setDefaultBackend] = useState<string | null>(null);

  // Fetch the global default backend so the "fresh session" warning exactly
  // matches the queue's resolution (which uses ``cli || task.cli ||
  // default_backend || "opencode"``). Without this, a task with no pinned
  // backend + the user picking "Reuse task backend" would warn falsely.
  useEffect(() => {
    api
      .getSettings()
      .then((s) => setDefaultBackend(s.default_backend ?? null))
      .catch(() => {});
  }, []);

  // The Model dropdown follows the Backend selected here (blank = the task's
  // own backend).
  useEffect(() => {
    let cancelled = false;
    api
      .getModels(cli || undefined)
      .then((m) => {
        if (!cancelled) setModels(m.models ?? []);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [cli]);

  // A fresh nonce per click (even for the same text) re-triggers this effect,
  // pre-filling the composer with the quoted final message and focusing it.
  // setState-in-effect is intentional: we need to mirror a parent-driven input
  // (the WaitingCard reply button) into the composer's local state, plus
  // imperatively focus + scroll the textarea. React's lint rule is overly
  // strict for this "prop-driven state reset" case.
  useEffect(() => {
    if (!prefill) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setText(prefill.text);
    const el = textareaRef.current;
    if (el) {
      el.focus();
      el.scrollIntoView?.({ block: "center" });
    }
  }, [prefill]);

  // Compare against the value the queue will actually use, not against the
  // task's pinned backend alone — a task with no pin resolves to
  // ``default_backend``, so "Reuse task backend" (cli = "") is NOT a change.
  const resolvedTaskCli = task.cli ?? defaultBackend ?? "opencode";
  const resolvedCurrentCli = cli || (task.cli ?? defaultBackend ?? "opencode");
  const backendChanged =
    cli !== "" && resolvedCurrentCli !== resolvedTaskCli;

  const hasPr = (task.prs?.length ?? 0) > 0 || task.pr_number != null;
  // "Address reviewers" only makes sense on the fixer task: a pr_review task's
  // session lives in the detached review worktree, so resuming it there would
  // never push a commit to the PR.
  const showAddressReviewers = hasPr && task.type !== "pr_review";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.postFollowup(task.id, text.trim(), {
        pat_name: patName || undefined,
        model: model || undefined,
        cli: cli || undefined,
      });
      setText("");
      onSent();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to send follow-up");
    } finally {
      setBusy(false);
    }
  }

  async function addressReviewers() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      // "Address the reviewers": resume the fixer with the PR's review comments
      // fetched + embedded by the server (F7.6).
      await api.postFollowup(task.id, "Address the reviewers' comments.", {
        include_reviews: true,
        pat_name: patName || undefined,
        model: model || undefined,
        cli: cli || undefined,
      });
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
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-ink-400">Credentials</span>
            <select value={patName} onChange={(e) => setPatName(e.target.value)} className="field">
              <option value="">Reuse task account</option>
              {accounts.map((a) => (
                <option key={a.name} value={a.name}>
                  {a.login ?? a.name} ({a.masked})
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-ink-400">Backend</span>
            <select value={cli} onChange={(e) => setCli(e.target.value)} className="field">
              <option value="">Reuse task backend</option>
              {["opencode", "codex", "claude"].map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-ink-400">Model</span>
            <select value={model} onChange={(e) => setModel(e.target.value)} className="field">
              <option value="">Reuse task model</option>
              {models.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
        </div>
        {backendChanged && (
          <p className="text-xs text-amber-300">
            Changing the backend starts a fresh session with the previous conversation included.
          </p>
        )}
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={3}
          placeholder="e.g. Address the reviewer comments, then update the README…"
          className="field resize-y"
        />
        <p className="text-[11px] leading-relaxed text-ink-600">
          Resume refreshes remote refs first, then continues your worktree&apos;s local
          commits; review worktrees move to the current PR head.
        </p>
        {error && <p className="text-xs text-red-400">{error}</p>}
        <div className="flex justify-end gap-2">
          {showAddressReviewers && (
            <button
              type="button"
              disabled={busy}
              onClick={addressReviewers}
              className="btn-ghost text-xs"
              title="Resume the fixer with the PR's current review comments (fetched + embedded)"
            >
              {busy ? "Sending…" : "Address reviewers"}
            </button>
          )}
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

const REVIEWER_STATUS_COLOR: Record<string, string> = {
  queued: "text-ink-400",
  running: "text-syrup-300",
  posted: "text-green-400",
  failed: "text-red-400",
};

function ReviewersCard({
  task,
  agents,
  assigning,
  assignError,
  onAssign,
}: {
  task: Task;
  agents: CatalogAgent[];
  assigning: boolean;
  assignError: string | null;
  onAssign: (agentId: string) => void;
}) {
  const reviewers = task.reviewers ?? [];
  const assigned = new Set(reviewers.map((r) => r.agent_id));
  const available = agents.filter((a) => !assigned.has(a.id));

  return (
    <section className="surface p-5 animate-fade-up">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="panel-title">Reviewers</h2>
        <span className="font-mono text-[11px] text-ink-500">
          {reviewers.filter((r) => r.status === "posted").length}/{reviewers.length} posted
        </span>
      </div>

      {reviewers.length > 0 ? (
        <ul className="divide-y divide-ink-800/70">
          {reviewers.map((r) => (
            <li key={r.id} className="flex items-center gap-3 py-2 text-sm">
              <span className={`font-mono ${REVIEWER_STATUS_COLOR[r.status] ?? "text-ink-400"}`}>
                {r.agent_name}
              </span>
              <span className="rounded-full border border-ink-800 px-2 py-0.5 text-[11px] text-ink-500">
                {r.status}
              </span>
              <span className="ml-auto flex items-center gap-2">
                {r.status === "posted" && task.pr_number != null && (
                  <a
                    className="font-mono text-xs text-syrup-400 hover:text-syrup-300"
                    href={`https://github.com/${task.repo_full_name ?? ""}/pull/${task.pr_number}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    PR #{task.pr_number} ↗
                  </a>
                )}
                <Link
                  to={`/tasks/${r.task_id}`}
                  className="font-mono text-xs text-ink-500 hover:text-ink-300"
                >
                  task #{r.task_id}
                </Link>
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mb-3 text-xs text-ink-500">
          No reviewers assigned yet. Assign catalog reviewers (kind <code className="font-mono">reviewer</code>) to review this PR — each runs its own review task and posts its comments.
        </p>
      )}

      {available.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-ink-800 pt-3">
          {available.map((a) => (
            <button
              key={a.id}
              disabled={assigning}
              onClick={() => onAssign(a.id)}
              className="btn-ghost !px-2.5 !py-1 text-xs"
            >
              + {a.name} ({a.id})
            </button>
          ))}
        </div>
      )}
      {assignError && <p className="mt-2 text-xs text-red-400">{assignError}</p>}
    </section>
  );
}

function ArtifactPreview({
  taskId,
  artifact,
  onClose,
}: {
  taskId: number;
  artifact: Artifact;
  onClose: () => void;
}) {
  const [content, setContent] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const ext = extOf(artifact.path);
  const isImage = IMAGE_EXTENSIONS.has(ext);
  const isText = TEXT_EXTENSIONS.has(ext);

  useEffect(() => {
    if (!isText) return;
    let cancelled = false;
    fetch(api.artifactContentUrl(taskId, artifact.id))
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .then((t) => {
        if (cancelled) return;
        if (t.includes("\u0000")) {
          setFailed(true);
        } else {
          setContent(t);
        }
      })
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, [taskId, artifact.id, isText]);

  useEffect(() => {
    // Focus the dialog and close on Escape. The listener is registered once
    // because onClose is a stable useCallback.
    const node = dialogRef.current;
    node?.focus();
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink-950/80 p-4"
      onClick={onClose}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={artifact.path}
        tabIndex={-1}
        className="surface flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden outline-none"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 border-b border-ink-800 px-5 py-3">
          <h3 className="min-w-0 flex-1 truncate font-mono text-sm text-ink-100">{artifact.path}</h3>
          <a
            href={api.artifactUrl(taskId, artifact.id)}
            className="btn-ghost !px-3 !py-1 text-xs"
          >
            Download
          </a>
          <button onClick={onClose} className="btn-ghost !px-2 !py-1 text-xs">
            ✕
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-5">
          {isImage ? (
            <img
              src={api.artifactContentUrl(taskId, artifact.id)}
              alt={artifact.path}
              className="max-h-full max-w-full"
            />
          ) : failed ? (
            <p className="text-sm text-ink-400">
              Preview unavailable for this file type — use Download.
            </p>
          ) : content === null ? (
            <p className="text-sm text-ink-500">Loading…</p>
          ) : (
            <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-ink-300">
              {content}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}

function useAutoScroll<T extends HTMLElement>(dep: unknown, enabled: boolean) {
  const ref = useRef<T | null>(null);
  useEffect(() => {
    if (!enabled || !ref.current) return;
    ref.current.scrollTop = ref.current.scrollHeight;
  }, [dep, enabled]);
  return ref;
}

function diffLineClass(line: string): string {
  // Git emits file headers as "--- a/..." / "+++ b/..." (with a space); a
  // content line merely starting with "---"/"+++" is a real removal/addition.
  if (line.startsWith("--- ") || line.startsWith("+++ ")) return "text-ink-400";
  if (line.startsWith("@@")) return "text-syrup-300";
  if (line.startsWith("+")) return "bg-green-500/10 text-green-300";
  if (line.startsWith("-")) return "bg-red-500/10 text-red-300";
  return "text-ink-300";
}

const STATUS_LETTER: Record<string, string> = {
  added: "A",
  removed: "D",
  modified: "M",
  renamed: "R",
  binary: "B",
};

function pathLabel(file: { oldPath: string | null; newPath: string | null; status: string }): string {
  const target = file.newPath ?? file.oldPath ?? "?";
  if (file.status === "renamed" && file.oldPath && file.newPath) {
    return `${file.oldPath} → ${file.newPath}`;
  }
  return target;
}

function DiffFileSection({
  file,
  defaultOpen,
}: {
  file: ReturnType<typeof parseUnifiedDiff>[number];
  defaultOpen: boolean;
}) {
  const { ref, inView } = useInView<HTMLDivElement>();
  const letter = STATUS_LETTER[file.status] ?? "M";
  return (
    <details open={defaultOpen}>
      <summary className="cursor-pointer select-none font-mono text-xs text-ink-200 transition-colors hover:text-syrup-300">
        <span
          className={`mr-2 inline-block w-4 text-center font-bold ${
            file.status === "added"
              ? "text-green-400"
              : file.status === "removed"
                ? "text-red-400"
                : file.status === "renamed"
                  ? "text-chai-300"
                  : file.status === "binary"
                    ? "text-ink-500"
                    : "text-syrup-300"
          }`}
        >
          {letter}
        </span>
        <span className="text-ink-100">{pathLabel(file)}</span>
        {file.additions > 0 && (
          <span className="ml-2 text-green-400">+{file.additions}</span>
        )}
        {file.deletions > 0 && (
          <span className="ml-2 text-red-400">−{file.deletions}</span>
        )}
      </summary>
      <div ref={ref}>
        {file.binary ? (
          <p className="mt-1 rounded bg-ink-900/60 p-3 font-mono text-[11px] text-ink-400">
            binary file — use Artifacts below to download.
          </p>
        ) : inView || typeof IntersectionObserver === "undefined" ? (
          <pre className="mt-1 max-h-96 overflow-auto whitespace-pre rounded bg-ink-900/60 p-2 font-mono text-[11px] leading-relaxed">
            {file.hunks.map((hunk, h) => (
              <div key={h}>
                {hunk.header && (
                  <div className={diffLineClass(hunk.header)}>{hunk.header}</div>
                )}
                {hunk.lines.map((line, j) => (
                  <div key={j} className={diffLineClass(line)}>
                    {line}
                  </div>
                ))}
              </div>
            ))}
          </pre>
        ) : (
          <p className="mt-1 rounded bg-ink-900/60 p-3 font-mono text-[11px] text-ink-500">
            scroll to load…
          </p>
        )}
      </div>
    </details>
  );
}

function DiffView({ diff }: { diff: string }) {
  const files = useMemo(() => parseUnifiedDiff(diff), [diff]);
  if (files.length === 0) {
    return <p className="text-sm text-ink-500">No diff.</p>;
  }
  const totals = files.reduce(
    (acc, f) => ({ add: acc.add + f.additions, del: acc.del + f.deletions }),
    { add: 0, del: 0 }
  );
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3 text-xs text-ink-500">
        <span>
          {files.length} file{files.length > 1 ? "s" : ""}
        </span>
        <span className="text-green-400">+{totals.add}</span>
        <span className="text-red-400">−{totals.del}</span>
      </div>
      {files.map((file, i) => (
        <DiffFileSection key={`${file.newPath ?? file.oldPath ?? i}`} file={file} defaultOpen={files.length === 1} />
      ))}
    </div>
  );
}

function DiffSection({
  taskId,
  run,
}: {
  taskId: number;
  run: Run;
}) {
  const [diff, setDiff] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!run.has_diff) return;
    let cancelled = false;
    api
      .getLiveDiff(taskId)
      .then((r) => !cancelled && setDiff(r.diff))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : "failed to load diff"));
    return () => {
      cancelled = true;
    };
  }, [taskId, run.has_diff, run.id]);

  if (!run.has_diff) return null;
  return (
    <section className="surface p-5 animate-fade-up">
      <h2 className="panel-title mb-3">Diff</h2>
      {error && <p className="text-xs text-red-400">{error}</p>}
      {diff === null ? (
        <p className="text-sm text-ink-500">Loading…</p>
      ) : (
        <DiffView diff={diff} />
      )}
    </section>
  );
}

export default function TaskDetail() {
  const { id } = useParams();
  const taskId = Number(id);
  const [task, setTask] = useState<Task | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [agents, setAgents] = useState<CatalogAgent[]>([]);
  const [assigning, setAssigning] = useState(false);
  const [assignError, setAssignError] = useState<string | null>(null);
  const [live, setLive] = useState<SseEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [followUpPending, setFollowUpPending] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [preview, setPreview] = useState<Artifact | null>(null);
  const [followScroll, setFollowScroll] = useState(true);
  const [actionBusy, setActionBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [publishDialogOpen, setPublishDialogOpen] = useState(false);
  const [publishOptions, setPublishOptions] = useState<{
    mode: "new_pr" | "update_pr" | "push_branch";
    branch?: string;
    pr_number?: number;
  } | null>(null);
  const closePreview = useCallback(() => setPreview(null), []);
  const actionInFlightRef = useRef(false);
  const lastRunIdRef = useRef<number | null>(null);
  const lastSeqRef = useRef(0);

  const load = useCallback(() => {
    api
      .getTask(taskId)
      .then((t) => {
        const runId = t.run?.id ?? null;
        if (lastRunIdRef.current !== runId) {
          const prior = lastRunIdRef.current;
          setLive([]);
          lastRunIdRef.current = runId;
          // New run → fresh per-run seq watermark (server scopes seq per run),
          // so a stale high watermark from an earlier run/process can't discard
          // this run's events (SSE backfill, F4).
          lastSeqRef.current = 0;
          // Auto-follow the latest run unless the user hand-picked an older one
          // (stale-closure + auto-advance fix, F8/M1). A selection that is null
          // (first load) or the PREVIOUS latest (watching the live stream) follows
          // the new run; a genuinely pinned older run stays put.
          setSelectedRunId((cur) => (cur === null || cur === prior ? runId : cur));
        }
        // After any run change, sync the watermark to persisted steps (run-end
        // reload) so replayed buffer events can't duplicate what we already have.
        lastSeqRef.current = Math.max(
          lastSeqRef.current,
          ...(t.run?.steps ?? []).map((s) => s.seq ?? 0)
        );
        setTask(t);
      })
      .catch((e) => setError(e.message));
    api
      .getRuns(taskId)
      .then((rs) => {
        setRuns(rs);
        setRunsError(null);
        setSelectedRunId((cur) => {
          if (cur === null || !rs.some((r) => r.id === cur)) {
            return rs.length > 0 ? rs[rs.length - 1].id : null;
          }
          return cur;
        });
      })
      .catch(() => setRunsError("Could not load run history."));
    api
      .getAgents(true)
      .then((a) => setAgents(a.filter((x) => x.kind === "reviewer")))
      .catch(() => {});
    api.getRepos().then(setRepos).catch(() => {});
    api.getTokens().then((t) => setAccounts(t.accounts ?? [])).catch(() => {});
  }, [taskId]);

  useEffect(() => {
    load();
  }, [load]);

  // Cancel / Re-run / Publish: serialized, with errors surfaced inline instead of
  // silently swallowed (D-4). The ref check is synchronous so two clicks in the
  // same tick (before React re-renders) cannot double-fire.
  function runAction(fn: () => Promise<unknown>) {    if (actionInFlightRef.current) return;
    actionInFlightRef.current = true;
    setActionBusy(true);
    setActionError(null);
    fn()
      .then(load)
      .catch((e) =>
        setActionError(e instanceof Error ? e.message : "action failed")
      )
      .finally(() => {
        actionInFlightRef.current = false;
        setActionBusy(false);
      });
  }

  // Delete: confirmed, then leave to the tasks list (this task no longer exists).
  async function deleteTask() {
    if (!task) return;
    if (!window.confirm(`Delete task ${task.id}? This removes its runs, artifacts and worktree.`)) {
      return;
    }
    await api.deleteTask(task.id);
    window.location.assign("/");
  }

  async function assignReviewer(agentId: string) {
    if (!task || !agentId || assigning) return;
    setAssigning(true);
    setAssignError(null);
    try {
      await api.assignReviewers(task.id, [agentId]);
      load();
    } catch (err) {
      setAssignError(err instanceof Error ? err.message : "failed to assign reviewer");
    } finally {
      setAssigning(false);
    }
  }

  const running = task !== null && !TERMINAL.has(task.status);
  const isLatest = selectedRunId === null || selectedRunId === task?.run?.id;

  const runId = task?.run?.id ?? null;
  useEffect(() => {
    if (!task || !running || !isLatest) return;
    const unsubscribe = taskEvents(
      taskId,
      (event) => {
        // Dedupe replayed/re-delivered events (after_seq backfill + EventSource
        // auto-reconnect): anything at or below the last seq we've seen is old.
        if (typeof event.seq === "number") {
          if (event.seq <= lastSeqRef.current) return;
          lastSeqRef.current = event.seq;
        }
        setLive((l) =>
          // Bound the live buffer (backend persists only the last 500 steps).
          l.length >= MAX_LIVE ? [...l.slice(l.length - MAX_LIVE + 1), event] : [...l, event]
        );
      },
      () => {
        setLive([]);
        load();
      },
      lastSeqRef.current
    );
    return unsubscribe;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId, runId, running, isLatest]);

  // After sending a follow-up, poll until a new run appears, then refresh.
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
          if (runId !== lastRunIdRef.current) {
            lastRunIdRef.current = runId;
            setLive([]);
            lastSeqRef.current = 0; // new run → fresh per-run watermark
            setTask(t);
            setSelectedRunId(runId);
            setFollowUpPending(false);
          }
        })
        .catch(() => {}); // poll failures just retry on the next tick
    }, 1500);
    return () => clearInterval(timer);
  }, [followUpPending, taskId]);

  // Hooks must run unconditionally — compute safe deps before the early returns.
  const previewRun = task
    ? (runs.find((r) => r.id === selectedRunId) ?? task.run ?? runs[runs.length - 1] ?? null)
    : null;
  const previewStepsLen = previewRun?.steps?.length ?? 0;
  const previewIsLatest = task !== null && (selectedRunId === null || selectedRunId === task.run?.id);
  const previewTimelineLen = previewIsLatest ? previewStepsLen + live.length : previewStepsLen;
  const timelineRef = useAutoScroll<HTMLDivElement>(previewTimelineLen, followScroll);
  const consoleRef = useAutoScroll<HTMLDivElement>(previewTimelineLen, followScroll);
  const [replyPrefill, setReplyPrefill] = useState<{ nonce: number; text: string } | null>(null);
  const replyNonce = useRef(0);
  // Phase 4 T6 — whether an IDE command is configured (for Open worktree).
  const [ideConfigured, setIdeConfigured] = useState(false);
  const [ideError, setIdeError] = useState<string | null>(null);
  const [ideBusy, setIdeBusy] = useState(false);

  // Fetch ide_command once to decide whether "Open worktree" is available.
  useEffect(() => {
    api
      .getSettings()
      .then((s) => setIdeConfigured(Boolean(s.ide_command)))
      .catch(() => {});
  }, []);

  const openInIde = () => {
    if (ideBusy || !task) return;
    setIdeBusy(true);
    setIdeError(null);
    api
      .openInIde(task.id)
      .then(() => {})
      .catch((e) =>
        setIdeError(e instanceof Error ? e.message : "failed to open in IDE")
      )
      .finally(() => setIdeBusy(false));
  };

  if (error) return <p className="text-red-400">{error}</p>;
  if (!task) return <p className="text-ink-500">Loading…</p>;

  const repoName = task.repo_full_name ?? repos.find((r) => r.id === task.repo_id)?.full_name ?? `repo#${task.repo_id}`;
  const selectedRun =
    runs.find((r) => r.id === selectedRunId) ?? task.run ?? runs[runs.length - 1] ?? null;
  const steps = selectedRun?.steps ?? [];
  const finalMessage = (() => {
    if (!selectedRun?.waiting_input) return null;
    for (let i = steps.length - 1; i >= 0; i--) {
      const s = steps[i];
      if (s.type === "message" && s.text) return s.text;
    }
    return null;
  })();
  const canReply = runs.some((r) => r.session_id) && TERMINAL.has(task.status);
  const timeline = isLatest ? [...steps, ...live] : steps;
  const consoleLines = timeline.filter((s) => s.type === "message" || s.type === "tool_call");

  const lastPhase = timeline.reduce<string | null>((acc, s) => s.phase ?? acc, null);
  const phaseIndex = lastPhase ? PHASE_ORDER.indexOf(lastPhase) : -1;

  function handleDismissAttention() {
    if (!task) return;
    api
      .dismissAttention(task.id)
      .then((updated) => setTask(updated))
      .catch((e) => setError(e.message));
  }

  return (
    <div className="space-y-6 animate-fade-up">
      <div className="flex flex-wrap items-center gap-3">
        <Link to="/" className="text-sm text-ink-500 transition-colors hover:text-syrup-300">
          ← Tasks
        </Link>
        <h1 className="text-2xl font-bold tracking-tight text-ink-100">Task #{task.id}</h1>
        <StatusBadge status={task.status} />
        {task.attention && task.attention !== "working" && (
          <div className="flex items-center gap-1.5">
            <AttentionBadge attention={task.attention} />
            {task.attention === "needs_you" && (
              <button
                type="button"
                onClick={handleDismissAttention}
                className="rounded px-1.5 py-0.5 text-[10px] font-medium text-ink-400 ring-1 ring-ink-700/60 hover:bg-ink-800 hover:text-ink-200 transition-colors"
                title="Dismiss attention for this task"
              >
                Dismiss
              </button>
            )}
          </div>
        )}
        <span className="mx-1 hidden h-4 w-px bg-ink-800 sm:block" />
        <span className="font-mono text-xs text-ink-500">
          {repoName}
          <span className="mx-1.5 text-ink-700">·</span>
          {task.model ?? "default model"}
          {task.pat_name && (
            <>
              <span className="mx-1.5 text-ink-700">·</span>
              {accounts.find((a) => a.name === task.pat_name)?.login ?? task.pat_name}
            </>
          )}
        </span>
      </div>

      {finalMessage && selectedRun && (
        <>
          <WaitingCard
            run={selectedRun}
            message={finalMessage}
            canReply={canReply}
            onReply={() => {
              const quoted = finalMessage.split("\n").map((l) => `> ${l}`).join("\n");
              replyNonce.current += 1;
              setReplyPrefill({ nonce: replyNonce.current, text: `${quoted}\n\n` });
            }}
            ideConfigured={ideConfigured}
            onOpenWorktree={openInIde}
            onReject={() => {
              if (window.confirm("Reject this proposal and dismiss attention?")) {
                handleDismissAttention();
              }
            }}
          />
          {ideError && <p className="text-xs text-red-400">{ideError}</p>}
        </>
      )}

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

        <div className="mt-4 flex flex-wrap gap-2 border-t border-ink-800 pt-4">
          {(task.prs?.length ? task.prs : task.pr_number ? [task.pr_number] : []).map((n) => (
            <a
              key={`pr-${n}`}
              className="btn-ghost !px-3 !py-1 text-xs"
              href={`https://github.com/${repoName}/pull/${n}`}
              target="_blank"
              rel="noreferrer"
            >
              PR #{n} ↗
            </a>
          ))}
          {(task.issues ?? []).map((n) => (
            <a
              key={`issue-${n}`}
              className="btn-ghost !px-3 !py-1 text-xs"
              href={`https://github.com/${repoName}/issues/${n}`}
              target="_blank"
              rel="noreferrer"
            >
              Issue #{n} ↗
            </a>
          ))}
          {task.check_run_id ? (
            <a
              className="btn-ghost !px-3 !py-1 text-xs text-syrup-300"
              href={`https://github.com/${repoName}/${
                task.pr_number ? `pull/${task.pr_number}` : `commits/${task.source_branch || task.target_branch || "main"}`
              }`}
              target="_blank"
              rel="noreferrer"
            >
              commit status
            </a>
          ) : null}
        </div>

        <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 border-t border-ink-800 pt-4 text-xs sm:grid-cols-4">
          <div>
            <dt className="text-ink-600">Type</dt>
            <dd className="mt-0.5 font-mono text-ink-300">{task.type}</dd>
          </div>
          <div>
            <dt className="text-ink-600">Publish</dt>
            <dd className="mt-0.5 font-mono text-ink-300">
              {task.publish_mode === "auto"
                ? "auto"
                : task.publish_mode === "manual"
                  ? "manual"
                  : "setting"}
            </dd>
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
        {(task.status === "needs_approval" || canManualPublish(task)) && (
          <MergeReadinessPanel taskId={task.id} refreshKey={task.updated_at} />
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        {(task.status === "running" || task.status === "queued") && (
          <Action
            onClick={() => runAction(() => api.cancelTask(task.id))}
            disabled={actionBusy}
          >
            Cancel
          </Action>
        )}
        {TERMINAL.has(task.status) && task.status !== "needs_approval" && (
          <Action
            onClick={() => runAction(() => api.rerunTask(task.id))}
            disabled={actionBusy}
          >
            Re-run
          </Action>
        )}
        {task.status === "needs_approval" && (
          <Action
            onClick={() => {
              setPublishOptions({ mode: "new_pr" });
              setPublishDialogOpen(true);
            }}
            disabled={actionBusy}
          >
            Publish
          </Action>
        )}
        {canManualPublish(task) && (
          <PublishButton
            task={task}
            disabled={actionBusy}
            onPick={(opts) => {
              setPublishOptions(opts);
              setPublishDialogOpen(true);
            }}
          />
        )}
        <Action
          onClick={deleteTask}
          disabled={actionBusy}
        >
          Delete
        </Action>
        {actionError && <p className="text-xs text-red-400">{actionError}</p>}
      </div>

      {(task.prs?.length || task.pr_number) && (
        <ReviewersCard
          task={task}
          agents={agents}
          assigning={assigning}
          assignError={assignError}
          onAssign={assignReviewer}
        />
      )}

      {runs.some((r) => r.session_id) && TERMINAL.has(task.status) && (
        <FollowUpComposer
          task={task}
          followups={task.followups ?? []}
          accounts={accounts}
          onSent={() => {
            load();
            setFollowUpPending(true);
          }}
          prefill={replyPrefill}
        />
      )}

      {selectedRun && selectedRun.artifacts && selectedRun.artifacts.length > 0 && (
        <section className="surface p-5 animate-fade-up">
          <h2 className="panel-title mb-3">Artifacts (run #{selectedRun.seq})</h2>
          <ul className="divide-y divide-ink-800/70">
            {selectedRun.artifacts.map((a) => (
              <li key={a.id} className="flex items-center gap-3 py-2">
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-chai-400" />
                <button
                  onClick={() => setPreview(a)}
                  className="min-w-0 flex-1 truncate text-left font-mono text-sm text-ink-200 transition-colors hover:text-syrup-300"
                >
                  {a.path}
                </button>
                <span className="shrink-0 font-mono text-[11px] text-ink-500">
                  {formatBytes(a.size)}
                </span>
                <a
                  href={api.artifactUrl(task.id, a.id)}
                  className="shrink-0 text-[11px] text-ink-500 transition-colors hover:text-syrup-300"
                >
                  download
                </a>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section className="surface flex h-[30rem] flex-col p-5">
          <div ref={timelineRef} className="min-h-0 flex-1 overflow-y-auto pr-2">
            <div className="sticky top-0 z-10 bg-ink-900/85 pb-3 backdrop-blur-sm">
              <div className="flex items-center justify-between gap-2">
                <h2 className="panel-title">Timeline</h2>
                <label className="flex shrink-0 items-center gap-1.5 text-[11px] text-ink-500">
                  <input
                    type="checkbox"
                    checked={followScroll}
                    onChange={(e) => setFollowScroll(e.target.checked)}
                    className="accent-syrup-500"
                  />
                  auto-scroll
                </label>
              </div>
            </div>
            <ol className="space-y-3 text-sm">
              {timeline.length === 0 && <li className="text-ink-600">No steps yet.</li>}
              {timeline.map((step, i) => (
                <TimelineItem
                  key={step.seq ?? `${step.ts ?? "?"}-${step.type}`}
                  step={step}
                  index={i}
                />
              ))}
            </ol>
          </div>
        </section>
        <section className="surface flex h-[30rem] flex-col p-5">
          <div ref={consoleRef} className="min-h-0 flex-1 overflow-y-auto pr-2">
            <div className="sticky top-0 z-10 bg-ink-900/85 pb-3 backdrop-blur-sm">
              <div className="flex items-center justify-between gap-2">
                <h2 className="panel-title">Console</h2>
                <span className="font-mono text-[11px] tabular-nums text-ink-500">
                  {consoleLines.length} {consoleLines.length === 1 ? "line" : "lines"}
                  {selectedRun?.started_at
                    ? ` · ${runDuration(selectedRun.started_at, selectedRun.finished_at)}`
                    : ""}
                </span>
              </div>
            </div>
            <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-ink-300">
              {consoleLines.length === 0 ? "No output yet." : ""}
              {consoleLines.map((line) => (
                <div key={line.seq ?? `${line.ts ?? "?"}-${line.type}`} className="flex gap-2">
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
          </div>
        </section>
      </div>

      {selectedRun && (
        <DiffSection key={selectedRun.id} taskId={task.id} run={selectedRun} />
      )}

      {runs.length > 1 && (
        <section className="surface p-5 animate-fade-up">
          <div className="mb-3 flex items-center justify-between gap-2">
            <h2 className="panel-title">Run history</h2>
            <p className="text-xs text-ink-500">
              The live stream follows the latest run. Click a run to view its logs, diff, and artifacts.
            </p>
          </div>
          <ol className="divide-y divide-ink-800/70">
            {runs.map((r) => {
              const active = r.id === selectedRun?.id;
              return (
                <li key={r.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedRunId(r.id)}
                    className={`flex w-full items-center gap-3 px-2 py-2.5 text-left transition-colors ${
                      active ? "rounded-lg bg-ink-850/80 ring-1 ring-inset ring-syrup-500/40" : "hover:bg-ink-850/40"
                    }`}
                  >
                    <span className="font-mono text-sm text-ink-200">#{r.seq}</span>
                    <StatusBadge status={r.status ?? "—"} />
                    <span className="hidden font-mono text-[11px] text-ink-500 sm:block">
                      {r.started_at ? formatRunTime(r.started_at) : "—"}
                    </span>
                    <span className="font-mono text-[11px] text-ink-400">
                      {runDuration(r.started_at, r.finished_at)}
                    </span>
                    {r.has_diff && (
                      <span className="rounded bg-ink-850 px-1.5 py-0.5 font-mono text-[10px] text-syrup-300">
                        diff
                      </span>
                    )}
                    {r.artifacts && r.artifacts.length > 0 && (
                      <span className="rounded bg-ink-850 px-1.5 py-0.5 font-mono text-[10px] text-chai-300">
                        {r.artifacts.length} artifact{r.artifacts.length > 1 ? "s" : ""}
                      </span>
                    )}
                    <span className="ml-auto hidden max-w-[16rem] truncate font-mono text-[11px] text-ink-500 md:block">
                      {r.model ?? r.cli ?? ""}
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
          {runsError && <p className="mt-2 text-xs text-red-400">{runsError}</p>}
        </section>
      )}

      {selectedRun && (
        <FileBrowser taskId={task.id} refreshSignal={live.length} />
      )}

      {preview && (
        <ArtifactPreview
          taskId={task.id}
          artifact={preview}
          onClose={closePreview}
        />
      )}

      {publishDialogOpen && publishOptions && (
        <PublishDialog
          taskId={task.id}
          options={publishOptions}
          onClose={() => {
            setPublishDialogOpen(false);
            setPublishOptions(null);
          }}
          onPublished={() => {
            runAction(async () => {
              // Reload the task so the PR number / status reflect the publish.
              await api.getTask(task.id).then((t) => setTask(t));
            });
          }}
        />
      )}
    </div>
  );
}
