import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, taskEvents } from "../api/client";
import { StatusBadge } from "../components/StatusBadge";
import type { Account, Artifact, Followup, Repo, Run, SseEvent, Task } from "../types";

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

const TEXT_EXTENSIONS = new Set([
  "txt", "md", "json", "log", "py", "js", "ts", "tsx", "jsx", "sh", "yaml", "yml",
  "toml", "csv", "html", "css", "go", "rs", "java", "c", "h", "cpp", "hpp",
]);
const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "ico", "bmp"]);

function Action({ onClick, children, disabled }: { onClick: () => void; children: string; disabled?: boolean }) {
  return (
    <button onClick={onClick} disabled={disabled} className="btn-ghost disabled:opacity-40">
      {children}
    </button>
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
        {step.type === "tool_call" ? (
          <div className="mt-0.5">
            <ToolCallEntry step={step} />
          </div>
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

function extOf(path: string): string {
  const i = path.lastIndexOf(".");
  return i >= 0 ? path.slice(i + 1).toLowerCase() : "";
}

function FollowUpComposer({
  task,
  followups,
  accounts,
  models,
  onSent,
}: {
  task: Task;
  followups: Followup[];
  accounts: Account[];
  models: string[];
  onSent: () => void;
}) {
  const [text, setText] = useState("");
  const [patName, setPatName] = useState("");
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.postFollowup(task.id, text.trim(), {
        pat_name: patName || undefined,
        model: model || undefined,
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
              <option value="">Default</option>
              {accounts.map((a) => (
                <option key={a.name} value={a.name}>
                  {a.login ?? a.name} ({a.masked})
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
  const ext = extOf(artifact.path);
  const isImage = IMAGE_EXTENSIONS.has(ext);
  const isText = TEXT_EXTENSIONS.has(ext);

  useEffect(() => {
    if (!isText) return;
    fetch(api.artifactContentUrl(taskId, artifact.id))
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .then((t) => {
        if (t.includes("\u0000")) {
          setFailed(true);
        } else {
          setContent(t);
        }
      })
      .catch(() => setFailed(true));
  }, [taskId, artifact.id, isText]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-950/80 p-4" onClick={onClose}>
      <div
        className="surface flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden"
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

export default function TaskDetail() {
  const { id } = useParams();
  const taskId = Number(id);
  const [task, setTask] = useState<Task | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [models, setModels] = useState<string[]>([]);
  const [live, setLive] = useState<SseEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [followUpPending, setFollowUpPending] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [preview, setPreview] = useState<Artifact | null>(null);
  const [followScroll, setFollowScroll] = useState(true);
  const lastRunIdRef = useRef<number | null>(null);

  const load = useCallback(() => {
    api
      .getTask(taskId)
      .then((t) => {
        const runId = t.run?.id ?? null;
        if (lastRunIdRef.current !== runId) {
          setLive([]);
          lastRunIdRef.current = runId;
          if (selectedRunId === null || !runs.some((r) => r.id === selectedRunId)) {
            // first load / new run → select the latest
            setSelectedRunId(runId);
          }
        }
        setTask(t);
      })
      .catch((e) => setError(e.message));
    api
      .getRuns(taskId)
      .then((rs) => {
        setRuns(rs);
        setSelectedRunId((cur) => {
          if (cur === null || !rs.some((r) => r.id === cur)) {
            return rs.length > 0 ? rs[rs.length - 1].id : null;
          }
          return cur;
        });
      })
      .catch(() => {});
    api.getRepos().then(setRepos).catch(() => {});
    api.getTokens().then((t) => setAccounts(t.accounts ?? [])).catch(() => {});
    api.getModels().then((m) => setModels(m.models ?? [])).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  useEffect(() => {
    load();
  }, [load]);

  const running = task !== null && !TERMINAL.has(task.status);
  const isLatest = selectedRunId === null || selectedRunId === task?.run?.id;

  useEffect(() => {
    if (!task || !running || !isLatest) return;
    const unsubscribe = taskEvents(
      taskId,
      (event) => setLive((l) => [...l, event]),
      () => {
        setLive([]);
        load();
      }
    );
    return unsubscribe;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId, task, running, isLatest]);

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
            setTask(t);
            setSelectedRunId(runId);
            setFollowUpPending(false);
          }
        })
        .catch(() => {});
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
  const timelineRef = useAutoScroll<HTMLOListElement>(previewTimelineLen, followScroll);
  const consoleRef = useAutoScroll<HTMLPreElement>(previewTimelineLen, followScroll);

  if (error) return <p className="text-red-400">{error}</p>;
  if (!task) return <p className="text-ink-500">Loading…</p>;

  const repoName = task.repo_full_name ?? repos.find((r) => r.id === task.repo_id)?.full_name ?? `repo#${task.repo_id}`;
  const selectedRun =
    runs.find((r) => r.id === selectedRunId) ?? task.run ?? runs[runs.length - 1] ?? null;
  const steps = selectedRun?.steps ?? [];
  const timeline = isLatest ? [...steps, ...live] : steps;
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
          {task.pat_name && (
            <>
              <span className="mx-1.5 text-ink-700">·</span>
              {accounts.find((a) => a.name === task.pat_name)?.login ?? task.pat_name}
            </>
          )}
        </span>
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
        {TERMINAL.has(task.status) && task.status !== "needs_approval" && (
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
          accounts={accounts}
          models={models}
          onSent={() => {
            load();
            setFollowUpPending(true);
          }}
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
        <section className="surface flex min-h-[24rem] flex-col p-5">
          <div className="mb-3 flex items-center justify-between gap-2">
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
          <ol ref={timelineRef} className="space-y-3 overflow-y-auto pr-2 text-sm">
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
          <pre ref={consoleRef} className="flex-1 overflow-y-auto whitespace-pre-wrap pr-2 font-mono text-xs leading-relaxed text-ink-300">
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

      {runs.length > 1 && (
        <section className="surface p-5 animate-fade-up">
          <div className="mb-3 flex items-center justify-between gap-2">
            <h2 className="panel-title">Runs</h2>
            <select
              value={selectedRunId ?? ""}
              onChange={(e) => setSelectedRunId(Number(e.target.value))}
              className="field w-auto !py-1 text-sm"
            >
              {runs.map((r) => (
                <option key={r.id} value={r.id}>
                  Run #{r.seq} — {r.status ?? "—"}
                  {r.model ? ` · ${r.model}` : ""}
                </option>
              ))}
            </select>
          </div>
          <p className="text-xs text-ink-500">
            Viewing run #{selectedRun?.seq ?? "?"} logs above. The live stream follows the latest run.
          </p>
        </section>
      )}

      {preview && <ArtifactPreview taskId={task.id} artifact={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}
