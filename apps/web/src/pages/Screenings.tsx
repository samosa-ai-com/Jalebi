import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { Repo, Screen, ScreenTemplate, ScreeningRun } from "../types";

const DEFAULT_CRON = "0 6 * * 1";

const CRON_PRESETS: { label: string; value: string }[] = [
  { label: "Every 5 min", value: "*/5 * * * *" },
  { label: "Hourly", value: "0 * * * *" },
  { label: "Daily 6 AM", value: "0 6 * * *" },
  { label: "Weekly Mon 6 AM", value: "0 6 * * 1" },
  { label: "Weekdays 9 AM", value: "0 9 * * 1-5" },
];

const SEVERITY_STYLES: Record<string, string> = {
  critical: "bg-red-500/15 text-red-300 ring-red-500/30",
  high: "bg-orange-500/15 text-orange-300 ring-orange-500/30",
  medium: "bg-amber-500/15 text-amber-300 ring-amber-500/30",
  low: "bg-sky-500/15 text-sky-300 ring-sky-500/30",
};

function StatusPill({ status }: { status: string }) {
  const map: Record<string, string> = {
    queued: "bg-ink-700/50 text-ink-300",
    running: "bg-syrup-500/15 text-syrup-300",
    done: "bg-green-500/15 text-green-300",
    failed: "bg-red-500/15 text-red-300",
  };
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${map[status] ?? "bg-ink-700/50 text-ink-300"}`}>
      {status}
    </span>
  );
}

function ScreenForm({
  repos,
  templates,
  editing,
  onSaved,
  onCancel,
}: {
  repos: Repo[];
  templates: ScreenTemplate[];
  editing: Screen | null;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const isEdit = editing !== null;
  const [name, setName] = useState(editing?.name ?? "");
  const [repoId, setRepoId] = useState<number | "">(editing?.repo_id ?? "");
  const [scopeBranch, setScopeBranch] = useState(editing?.scope_branch ?? "");
  const [cron, setCron] = useState(editing?.cadence_cron ?? DEFAULT_CRON);
  const [systemPrompt, setSystemPrompt] = useState(
    editing?.system_prompt ?? (templates[0]?.system_prompt ?? "")
  );
  const [cli, setCli] = useState(editing?.cli ?? "");
  const [model, setModel] = useState(editing?.model ?? "");
  const [enabled, setEnabled] = useState(editing?.enabled ?? true);
  const [notify, setNotify] = useState(editing?.notify_ntfy ?? true);
  const [tpl, setTpl] = useState("");
  const [branches, setBranches] = useState<string[]>([]);
  const [models, setModels] = useState<string[]>([]);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  // Models for the Model dropdown (best-effort — a failure just means no options).
  useEffect(() => {
    api
      .getModels()
      .then((m) => setModels(m.models ?? []))
      .catch(() => {});
  }, []);

  // Branch dropdown for the selected repo (best-effort).
  const repoIdNum = repoId === "" ? null : Number(repoId);
  useEffect(() => {
    if (repoIdNum == null) return;
    let cancelled = false;
    api
      .getRepoBranches(repoIdNum)
      .then((r) => !cancelled && setBranches(r.branches ?? []))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [repoIdNum]);

  function applyTemplate(t: ScreenTemplate | undefined) {
    if (!t) return;
    setName(t.name);
    setCron(t.cadence_cron);
    setSystemPrompt(t.system_prompt);
    setTpl(t.name);
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || repoId === "" || !systemPrompt.trim() || !cron.trim()) {
      setMsg({ kind: "err", text: "name, repo, system prompt, and cadence are required" });
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const input = {
        name: name.trim(),
        repo_id: Number(repoId),
        scope_branch: scopeBranch.trim() || null,
        system_prompt: systemPrompt.trim(),
        cadence_cron: cron.trim(),
        cli: cli || null,
        model: model || null,
        enabled,
        notify_ntfy: notify,
      };
      if (isEdit) await api.updateScreen(editing.id, input);
      else await api.createScreen(input);
      setMsg({ kind: "ok", text: "saved" });
      onSaved();
    } catch (err) {
      setMsg({ kind: "err", text: err instanceof Error ? err.message : "failed to save" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save} className="surface space-y-4 p-6">
      <div className="flex items-baseline justify-between">
        <h2 className="panel-title">{isEdit ? `Edit screen — ${editing.name}` : "New screen"}</h2>
        <button type="button" onClick={onCancel} className="btn-ghost text-xs">
          Cancel
        </button>
      </div>

      {!isEdit && templates.length > 0 && (
        <div>
          <span className="mb-1.5 block text-xs font-medium text-ink-400">Starter template</span>
          <select
            value={tpl}
            onChange={(e) => {
              const t = templates.find((x) => x.name === e.target.value);
              applyTemplate(t);
            }}
            className="field"
          >
            <option value="">Pick a starter screen…</option>
            {templates.map((t) => (
              <option key={t.name} value={t.name}>
                {t.name}
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <label>
          <span className="mb-1.5 block text-xs font-medium text-ink-400">Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} className="field" />
        </label>
        <label>
          <span className="mb-1.5 block text-xs font-medium text-ink-400">Repo</span>
          <select
            value={repoId}
            onChange={(e) => {
              setRepoId(e.target.value === "" ? "" : Number(e.target.value));
              setScopeBranch("");
            }}
            className="field"
            disabled={isEdit}
          >
            <option value="">Select repo…</option>
            {repos.map((r) => (
              <option key={r.id} value={r.id}>
                {r.full_name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <label>
          <span className="mb-1.5 block text-xs font-medium text-ink-400">
            Scope branch <span className="text-ink-600">(blank = default)</span>
          </span>
          <select
            value={scopeBranch}
            onChange={(e) => setScopeBranch(e.target.value)}
            className="field font-mono"
            disabled={repoId === ""}
          >
            <option value="">default branch</option>
            {branches.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
            {scopeBranch && !branches.includes(scopeBranch) && (
              <option value={scopeBranch}>{scopeBranch}</option>
            )}
          </select>
        </label>
        <label>
          <span className="mb-1.5 block text-xs font-medium text-ink-400">
            Cadence (5-field cron)
          </span>
          <input value={cron} onChange={(e) => setCron(e.target.value)} className="field font-mono" />
        </label>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {CRON_PRESETS.map((p) => (
          <button
            key={p.value}
            type="button"
            onClick={() => setCron(p.value)}
            className="btn-ghost !px-2.5 !py-1 text-xs"
          >
            {p.label}
          </button>
        ))}
        <span className="text-xs text-ink-500">— or type a cron like `30 1 * * *` (1:30 AM local).</span>
      </div>

      <label>
        <span className="mb-1.5 block text-xs font-medium text-ink-400">System prompt</span>
        <textarea
          value={systemPrompt}
          onChange={(e) => setSystemPrompt(e.target.value)}
          rows={5}
          className="field resize-y"
        />
      </label>

      <div className="grid gap-4 sm:grid-cols-2">
        <label>
          <span className="mb-1.5 block text-xs font-medium text-ink-400">Backend</span>
          <select value={cli} onChange={(e) => setCli(e.target.value)} className="field">
            <option value="">default (opencode)</option>
            <option value="opencode">opencode</option>
          </select>
        </label>
        <label>
          <span className="mb-1.5 block text-xs font-medium text-ink-400">Model</span>
          <select value={model} onChange={(e) => setModel(e.target.value)} className="field">
            <option value="">default (CLI default)</option>
            {models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
            {model && !models.includes(model) && <option value={model}>{model}</option>}
          </select>
        </label>
      </div>

      <div className="flex flex-wrap items-center gap-6">
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          Enabled
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={notify} onChange={(e) => setNotify(e.target.checked)} />
          Notify on findings (ntfy)
        </label>
      </div>

      {msg && (
        <p className={`text-sm ${msg.kind === "ok" ? "text-green-400" : "text-red-400"}`}>
          {msg.text}
        </p>
      )}

      <button type="submit" disabled={busy} className="btn-primary">
        {busy ? "Saving…" : isEdit ? "Save changes" : "Create screen"}
      </button>
    </form>
  );
}

function RunHistory({ screen }: { screen: Screen }) {
  const [runs, setRuns] = useState<ScreeningRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);

  const load = useCallback(() => {
    api
      .getScreenRuns(screen.id)
      .then((r) => mounted.current && setRuns(r))
      .catch((e) => mounted.current && setError(e.message));
  }, [screen.id]);

  useEffect(() => {
    mounted.current = true;
    load();
    // Poll so a running run's status flips to done without a manual refresh.
    const id = window.setInterval(load, 5000);
    return () => {
      mounted.current = false;
      window.clearInterval(id);
    };
  }, [load]);

  if (runs.length === 0) {
    return <p className="text-sm text-ink-500">No runs yet.</p>;
  }

  return (
    <div className="space-y-3">
      {runs.slice(0, 10).map((run) => {
        const active = run.status === "running" || run.status === "queued";
        return (
          <div key={run.id} className="rounded-lg border border-ink-800 p-3">
            <div className="flex items-center gap-3">
              <StatusPill status={run.status} />
              <span className="font-mono text-xs text-ink-500">
                {run.head_sha ? run.head_sha.slice(0, 12) : "—"}
              </span>
              <span className="ml-auto text-xs text-ink-500">
                {run.started_at ? new Date(run.started_at).toLocaleString() : ""}
              </span>
            </div>
            {active ? (
              <p className="mt-2 text-xs text-syrup-300">
                <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-syrup-400 align-middle" />{" "}
                Running… check back shortly.
              </p>
            ) : run.findings.length > 0 ? (
              <ul className="mt-3 space-y-2">
                {run.findings.map((f, i) => (
                  <li key={i} className="flex flex-col gap-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span
                        className={`rounded px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset ${SEVERITY_STYLES[f.severity] ?? SEVERITY_STYLES.medium}`}
                      >
                        {f.severity}
                      </span>
                      <span className="text-sm text-ink-200">{f.title}</span>
                      {f.file && (
                        <span className="font-mono text-xs text-ink-500">
                          {f.file}
                          {f.line != null ? `:${f.line}` : ""}
                        </span>
                      )}
                    </div>
                    {f.detail && <p className="text-xs text-ink-400">{f.detail}</p>}
                    {f.recommendation && (
                      <p className="text-xs text-ink-500">
                        <span className="text-ink-400">Recommendation:</span> {f.recommendation}
                      </p>
                    )}
                    <button
                      type="button"
                      className="btn-ghost mt-1 w-fit !px-2 !py-1 text-xs"
                      onClick={async () => {
                        try {
                          await api.createTask({
                            repo_id: screen.repo_id,
                            type: "screen_finding",
                            prompt: `Fix this ${f.severity} finding from the "${screen.name}" screen${
                              f.file ? ` in ${f.file}${f.line != null ? `:${f.line}` : ""}` : ""
                            }:\n\n${f.title}\n\n${f.detail ?? ""}${
                              f.recommendation ? `\n\nRecommended: ${f.recommendation}` : ""
                            }`,
                            target_branch: screen.scope_branch ?? undefined,
                            publish_mode: "manual",
                          });
                          window.alert("Created a screen_finding task.");
                        } catch (err) {
                          window.alert(err instanceof Error ? err.message : "failed to create task");
                        }
                      }}
                    >
                      New task from finding
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-xs text-ink-500">
                {run.error ? `Error: ${run.error}` : "No findings."}
              </p>
            )}
          </div>
        );
      })}
      {error && <p className="text-sm text-red-400">{error}</p>}
    </div>
  );
}

function ScreenCard({
  screen,
  repos,
  onEdited,
  onDeleted,
  onOpen,
  onEdit,
}: {
  screen: Screen;
  repos: Repo[];
  onEdited: () => void;
  onDeleted: () => void;
  onOpen: () => void;
  onEdit: () => void;
}) {
  const [running, setRunning] = useState(false);
  const repo = repos.find((r) => r.id === screen.repo_id);

  async function runNow() {
    setRunning(true);
    try {
      await api.runScreen(screen.id);
      window.setTimeout(onEdited, 300);
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "failed to run");
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="surface p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-ink-100">{screen.name}</h3>
          <p className="mt-0.5 text-xs text-ink-500">
            {repo?.full_name ?? `repo #${screen.repo_id}`}
            {screen.scope_branch ? ` · ${screen.scope_branch}` : " · default"}
          </p>
        </div>
        <div className="flex items-center gap-1">
          <span
            title={screen.enabled ? "Enabled" : "Disabled"}
            className={`h-2.5 w-2.5 rounded-full ${screen.enabled ? "bg-green-400" : "bg-ink-600"}`}
          />
          <span
            title={screen.notify_ntfy ? "ntfy on" : "ntfy off"}
            className={`h-2.5 w-2.5 rounded-full ${screen.notify_ntfy ? "bg-green-400" : "bg-ink-600"}`}
          />
        </div>
      </div>

      <p className="mt-2 font-mono text-xs text-ink-500">{screen.cadence_cron}</p>

      <div className="mt-4 flex items-center gap-2">
        <button type="button" onClick={runNow} disabled={running} className="btn-ghost !px-2.5 !py-1 text-xs">
          {running ? "Starting…" : "Run now"}
        </button>
        <button type="button" onClick={onOpen} className="btn-ghost !px-2.5 !py-1 text-xs">
          History
        </button>
        <button type="button" onClick={onEdit} className="btn-ghost !px-2.5 !py-1 text-xs">
          Edit
        </button>
        <button
          type="button"
          onClick={() => {
            if (window.confirm(`Delete screen "${screen.name}"?`)) {
              api
                .deleteScreen(screen.id)
                .then(onDeleted)
                .catch((e) => window.alert(e.message));
            }
          }}
          className="btn-ghost !px-2.5 !py-1 text-xs text-red-400"
        >
          Delete
        </button>
      </div>
    </div>
  );
}

export default function Screenings() {
  const [screens, setScreens] = useState<Screen[]>([]);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [templates, setTemplates] = useState<ScreenTemplate[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Screen | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [openRuns, setOpenRuns] = useState<number | null>(null);

  const load = useCallback(() => {
    api
      .getScreens()
      .then(setScreens)
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    load();
    api.getRepos().then(setRepos).catch(() => {});
    api.getScreenTemplates().then(setTemplates).catch(() => {});
  }, [load]);

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between animate-fade-up">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-ink-100">Screenings</h1>
          <p className="mt-1 text-sm text-ink-500">
            Scheduled, read-only code audits. Screening finds and notifies — it never acts. Turn
            findings into tasks yourself.
          </p>
        </div>
        <button
          type="button"
          className="btn-primary"
          onClick={() => {
            setEditing(null);
            setShowForm(true);
          }}
        >
          New screen
        </button>
      </header>

      {error && <p className="text-sm text-red-400">{error}</p>}

      {showForm && (
        <div className="animate-fade-up">
          <ScreenForm
            key={editing?.id ?? "new"}
            repos={repos}
            templates={templates}
            editing={editing}
            onSaved={() => {
              setShowForm(false);
              setEditing(null);
              load();
            }}
            onCancel={() => {
              setShowForm(false);
              setEditing(null);
            }}
          />
        </div>
      )}

      {screens.length === 0 && !showForm ? (
        <div className="surface flex flex-col items-start gap-3 p-6 animate-fade-up">
          <h2 className="panel-title">No screens yet</h2>
          <p className="text-sm text-ink-400">
            Create a screen to audit a connected repo on a schedule (e.g. security posture, docs
            drift). Pick a starter template — each is user-editable.
          </p>
          <button
            type="button"
            className="btn-primary"
            onClick={() => {
              setEditing(null);
              setShowForm(true);
            }}
          >
            Create your first screen
          </button>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {screens.map((s) => (
            <div key={s.id}>
              <ScreenCard
                screen={s}
                repos={repos}
                onEdited={load}
                onDeleted={load}
                onOpen={() => setOpenRuns(openRuns === s.id ? null : s.id)}
                onEdit={() => {
                  setEditing(s);
                  setShowForm(true);
                }}
              />
              {openRuns === s.id && (
                <div className="mt-2 rounded-lg border border-ink-800 p-4 animate-fade-up">
                  <RunHistory screen={s} />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
