import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import SearchableSelect from "../components/SearchableSelect";
import { useBackends } from "../hooks/useBackends";
import {
  clearLegacyDealt,
  findingFp,
  groupFpsByScreen,
  isDealtImported,
  readLegacyDealt,
  setDealtImported,
} from "../lib/screeningDealt";
import {
  buildFindingPrompt,
  buildMultiFindingPrompt,
  gateBatch,
  qualifiedScreenName,
  type BatchGate,
  type FindingEntry,
} from "../lib/screeningPrompt";
import type { ScreeningHandoff, TaskPrefill } from "./Tasks";
import type {
  Finding,
  Repo,
  Screen,
  ScreenTemplate,
  ScreeningFinding,
  ScreeningRun,
} from "../types";

export const SCREENS_SEEN_KEY = "jalebi-screens-seen-at";

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
    <span
      className={`rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${map[status] ?? "bg-ink-700/50 text-ink-300"}`}
    >
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
    editing?.system_prompt ?? templates[0]?.system_prompt ?? ""
  );
  const [cli, setCli] = useState(editing?.cli ?? "");
  const backendOptions = useBackends();
  const [model, setModel] = useState(editing?.model ?? "");
  const [enabled, setEnabled] = useState(editing?.enabled ?? true);
  const [notify, setNotify] = useState(editing?.notify_ntfy ?? true);
  const [tpl, setTpl] = useState("");
  const [branches, setBranches] = useState<string[]>([]);
  const [models, setModels] = useState<string[]>([]);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [branchesError, setBranchesError] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  // Models for the Model dropdown — follow the Backend selected in THIS form
  // (blank backend = the global default_backend default). Best-effort: a failure just
  // means no options. The cancelled guard drops a stale response if the backend
  // changes again mid-fetch.
  useEffect(() => {
    let cancelled = false;
    api
      .getModels(cli || undefined)
      .then((m) => {
        if (cancelled) return;
        setModels(m.models ?? []);
        setModelsError(null);
      })
      .catch((e) => {
        if (!cancelled) setModelsError(e instanceof Error ? e.message : "failed to load models");
      });
    return () => {
      cancelled = true;
    };
  }, [cli]);

  // Branch dropdown for the selected repo (best-effort).
  const repoIdNum = repoId === "" ? null : Number(repoId);
  useEffect(() => {
    if (repoIdNum == null) return;
    let cancelled = false;
    api
      .getRepoBranches(repoIdNum)
      .then((r) => {
        if (cancelled) return;
        setBranches(r.branches ?? []);
        setBranchesError(null);
      })
      .catch((e) => {
        if (!cancelled)
          setBranchesError(e instanceof Error ? e.message : "failed to load branches");
      });
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
        <SearchableSelect
          label="Starter template"
          value={tpl}
          onChange={(v) => {
            const t = templates.find((x) => x.name === v);
            applyTemplate(t);
          }}
          placeholder="Pick a starter screen…"
          options={templates.map((t) => t.name)}
        />
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <label>
          <span className="mb-1.5 block text-xs font-medium text-ink-400">Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} className="field" />
        </label>
        <SearchableSelect
          label="Repo"
          value={repoId}
          onChange={(v) => {
            setRepoId(v === "" ? "" : Number(v));
            setScopeBranch("");
          }}
          placeholder="Select repo…"
          disabled={isEdit}
          options={repos.map((r) => ({ value: String(r.id), label: r.full_name }))}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <SearchableSelect
            label="Scope branch (blank = default)"
            value={scopeBranch}
            onChange={setScopeBranch}
            placeholder="default branch"
            disabled={repoId === ""}
            options={branches}
          />
          {branchesError && (
            <span className="mt-1 block text-[11px] text-amber-400">
              Branch list failed to load ({branchesError}) — the default branch applies.
            </span>
          )}
        </div>
        <label>
          <span className="mb-1.5 block text-xs font-medium text-ink-400">
            Cadence (5-field cron)
          </span>
          <input
            value={cron}
            onChange={(e) => setCron(e.target.value)}
            className="field font-mono"
          />
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
        <span className="text-xs text-ink-500">
          — or type a cron like `30 1 * * *` (1:30 AM local).
        </span>
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
        <SearchableSelect
          label="Backend"
          value={cli}
          onChange={(v) => {
            setCli(v);
            // The old model pin belonged to the old backend — drop it rather
            // than running an invalid combination (the server does the same).
            setModel("");
          }}
          placeholder="default (global setting)"
          options={backendOptions}
        />
        <div>
          <SearchableSelect
            label="Model"
            value={model}
            onChange={setModel}
            placeholder="default (CLI default)"
            options={models}
            allowCustom
          />
          {modelsError && (
            <span className="mt-1 block text-[11px] text-amber-400">
              Model list failed to load ({modelsError}) — a saved pin still applies.
            </span>
          )}
        </div>
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

/** Build the Tasks-page prefill for one batch (single or multi). */
function batchPrefill(entries: FindingEntry[], gate: BatchGate): TaskPrefill {
  const prompt =
    entries.length === 1
      ? buildFindingPrompt(entries[0].screen, entries[0].finding, entries[0].repoFullName)
      : buildMultiFindingPrompt(entries);
  return {
    repoId: gate.repoId,
    type: "freeform",
    prompt,
    targetBranch: gate.targetBranch,
    publishMode: "manual",
  };
}

/** Navigate to the New-task form with the prompt injected. Findings are
 * marked dealt only after the task is actually created (Tasks.handleCreated). */
function sendToNewTask(
  navigate: ReturnType<typeof useNavigate>,
  entries: FindingEntry[],
  gate: BatchGate
): void {
  const handoff: ScreeningHandoff = {
    prefill: batchPrefill(entries, gate),
    dealtFps: entries.map((e) => findingFp(e.screen.id, e.finding)),
    screeningHandoffId: crypto.randomUUID(),
  };
  navigate("/", { state: { ...handoff, from: "screenings" } });
}

function RunHistory({
  screen,
  repos,
  onChanged,
}: {
  screen: Screen;
  repos: Repo[];
  onChanged: () => void;
}) {
  const navigate = useNavigate();
  const [runs, setRuns] = useState<ScreeningRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  // Batch selection across the visible runs: `${run.id}:${index}`.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [historyDealtError, setHistoryDealtError] = useState<string | null>(null);
  const mounted = useRef(true);
  const prevStatuses = useRef<string>("");

  const load = useCallback(() => {
    api
      .getScreenRuns(screen.id)
      .then((r) => {
        if (!mounted.current) return;
        setRuns(r);
        setError(null);
        const sig = r.map((x) => `${x.id}:${x.status}`).join(",");
        if (prevStatuses.current && prevStatuses.current !== sig) onChanged();
        prevStatuses.current = sig;
      })
      .catch((e) => mounted.current && setError(e.message));
  }, [screen.id, onChanged]);

  const hasActive = runs.some((r) => r.status === "running" || r.status === "queued");

  useEffect(() => {
    mounted.current = true;
    prevStatuses.current = "";
    load();
    // Poll fast while a run is live so status flips promptly; back off when
    // idle (scheduler ticks are minutes apart).
    const id = window.setInterval(load, hasActive ? 5000 : 30000);
    return () => {
      mounted.current = false;
      window.clearInterval(id);
    };
  }, [load, hasActive]);

  if (error && runs.length === 0) {
    return <p className="text-sm text-red-400">Run history failed to load: {error}</p>;
  }
  if (runs.length === 0) {
    return <p className="text-sm text-ink-500">No runs yet.</p>;
  }

  const visible = showAll ? runs.slice(0, 50) : runs.slice(0, 10);

  function toggleSelected(key: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function selectedEntries(): { key: string; finding: Finding }[] {
    const out: { key: string; finding: Finding }[] = [];
    for (const run of visible) {
      if (run.status === "running" || run.status === "queued") continue;
      run.findings.forEach((f, i) => {
        const key = `${run.id}:${i}`;
        if (selected.has(key)) out.push({ key, finding: f });
      });
    }
    return out;
  }

  function openBatchTask() {
    const repoFullName = repos.find((r) => r.id === screen.repo_id)?.full_name;
    const entries = selectedEntries().map(({ finding }) => ({ screen, finding, repoFullName }));
    sendToNewTask(navigate, entries, gateBatch(entries, repos));
    setSelected(new Set());
  }

  function markSelectedDealt() {
    const fps = selectedEntries().map(({ finding }) => findingFp(screen.id, finding));
    if (fps.length === 0) return;
    // Optimistic: clear selection now; revert nothing on failure (the rows
    // stay visible, and the error explains the retry).
    setSelected(new Set());
    setHistoryDealtError(null);
    api.markDealt(screen.id, fps).catch(() => {
      setHistoryDealtError("Could not mark dealt — retry from the Findings tab.");
    });
  }

  return (
    <div className="space-y-3">
      {selected.size > 0 && (
        <div
          className="flex flex-wrap items-center gap-2 rounded-lg border border-syrup-500/40 bg-syrup-500/5 px-3 py-2"
          role="region"
          aria-label="Selected findings actions"
        >
          <span className="text-xs text-syrup-300">{selected.size} selected</span>
          <button type="button" className="btn-ghost !px-2 !py-1 text-xs" onClick={openBatchTask}>
            {selected.size === 1
              ? "Open new task with finding"
              : `Open new task with ${selected.size} findings`}
          </button>
          <button
            type="button"
            className="btn-ghost !px-2 !py-1 text-xs"
            onClick={markSelectedDealt}
          >
            Mark dealt ({selected.size})
          </button>
          <button
            type="button"
            className="btn-ghost !px-2 !py-1 text-xs"
            onClick={() => setSelected(new Set())}
          >
            Clear
          </button>
        </div>
      )}
      {historyDealtError && <p className="text-xs text-red-400">{historyDealtError}</p>}
      {visible.map((run) => {
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
                {run.findings.map((f, i) => {
                  const key = `${run.id}:${i}`;
                  const checked = selected.has(key);
                  return (
                    <li key={i} className="flex flex-col gap-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleSelected(key)}
                          onClick={(e) => e.stopPropagation()}
                          aria-label={`Select finding ${f.title}`}
                          className="h-3.5 w-3.5 accent-amber-500"
                        />
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
                        onClick={() => {
                          const repoFullName = repos.find(
                            (r) => r.id === screen.repo_id
                          )?.full_name;
                          const entry = { screen, finding: f, repoFullName };
                          sendToNewTask(navigate, [entry], gateBatch([entry], repos));
                        }}
                      >
                        New task from finding
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="mt-2 text-xs text-ink-500">
                {run.error ? `Error: ${run.error}` : "No findings."}
              </p>
            )}
          </div>
        );
      })}
      {!showAll && runs.length > 10 && (
        <button
          type="button"
          onClick={() => setShowAll(true)}
          className="btn-ghost !px-2.5 !py-1 text-xs"
        >
          Show all {Math.min(runs.length, 50)} runs
        </button>
      )}
      {error && <p className="text-sm text-red-400">{error}</p>}
    </div>
  );
}

function toFinding(f: ScreeningFinding): Finding {
  return {
    severity: (["critical", "high", "medium", "low"] as const).includes(
      f.severity as "critical" | "high" | "medium" | "low"
    )
      ? (f.severity as "critical" | "high" | "medium" | "low")
      : "medium",
    title: f.title || "(untitled)",
    file: f.file,
    line: f.line,
    detail: f.detail,
    recommendation: f.recommendation,
  };
}

function FindingsInbox({ screens, repos }: { screens: Screen[]; repos: Repo[] }) {
  const navigate = useNavigate();
  const [items, setItems] = useState<ScreeningFinding[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [severity, setSeverity] = useState("all");
  const [screenFilter, setScreenFilter] = useState<number | "all">("all");
  const [query, setQuery] = useState("");
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [hideDealt, setHideDealt] = useState(true);
  // Server-authoritative dealt set, seeded instantly from the legacy
  // browser cache (no dealt flash) and revalidated from the API below.
  const [dealt, setDealt] = useState<Set<string>>(() => readLegacyDealt());
  const [dealtError, setDealtError] = useState<string | null>(null);
  // Batch selection: keys are `${run_id}:${screen_id}:${items-index}`.
  // Resolved through entryByKey (rebuilt every render) so keys stay exact
  // even with duplicate findings; cleared on refetch.
  const [selected, setSelected] = useState<Set<string>>(() => new Set());

  // One-time migration of the legacy browser-local dealt set, then server
  // revalidation. The imported flag is set only after every screen's import
  // POST succeeds, so a failure retries on the next mount instead of losing
  // state. Unknown (deleted) screens are skipped — their findings are gone.
  useEffect(() => {
    if (screens.length === 0) return;
    let cancelled = false;
    (async () => {
      try {
        const legacy = readLegacyDealt();
        if (legacy.size > 0 && !isDealtImported()) {
          const known = new Set(screens.map((s) => s.id));
          const targets = [...groupFpsByScreen(legacy)].filter(([sid]) => known.has(sid));
          await Promise.all(targets.map(([sid, fps]) => api.importDealt(sid, fps)));
          if (!cancelled) {
            setDealtImported();
            clearLegacyDealt();
          }
        }
        const perScreen = await Promise.all(
          screens.map((s) => api.getDealt(s.id).catch(() => ({ fingerprints: [] as string[] })))
        );
        if (!cancelled) setDealt(new Set(perScreen.flatMap((r) => r.fingerprints)));
      } catch {
        // Offline/server error: keep the cached set; dealt actions retry.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [screens]);

  useEffect(() => {
    let cancelled = false;
    api
      .getRecentFindings({
        limit: 100,
        severity: severity === "all" ? undefined : severity,
        screen_id: screenFilter === "all" ? undefined : screenFilter,
      })
      .then((r) => {
        if (!cancelled) {
          setItems(r);
          setSelected(new Set());
          setLoading(false);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "failed to load findings");
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [severity, screenFilter]);

  const screenById = (id: number) => screens.find((s) => s.id === id) ?? null;

  const fpOf = (f: ScreeningFinding) =>
    findingFp(f.screen_id, { title: f.title, file: f.file, line: f.line });

  function toggleDealt(fp: string, screenId: number) {
    const reopen = dealt.has(fp);
    // Optimistic update with rollback: the row hides/shows instantly, and a
    // failed POST restores the previous set plus an inline error.
    setDealt((prev) => {
      const next = new Set(prev);
      if (reopen) next.delete(fp);
      else next.add(fp);
      return next;
    });
    setDealtError(null);
    const call = reopen ? api.reopenDealt(screenId, [fp]) : api.markDealt(screenId, [fp]);
    call.catch(() => {
      setDealt((prev) => {
        const next = new Set(prev);
        if (reopen) next.add(fp);
        else next.delete(fp);
        return next;
      });
      setDealtError("Could not update dealt state — retry.");
    });
  }

  function toggleSelected(key: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  const dealtCount = items.filter((f) => dealt.has(fpOf(f))).length;

  const rows = items
    .map((f, i) => ({ f, key: `${f.run_id}:${f.screen_id}:${i}` }))
    .filter(({ f }) => {
      if (hideDealt && dealt.has(fpOf(f))) return false;
      const q = query.trim().toLowerCase();
      if (!q) return true;
      return (
        (f.title || "").toLowerCase().includes(q) ||
        (f.file || "").toLowerCase().includes(q) ||
        (f.screen_name || "").toLowerCase().includes(q)
      );
    });
  const visible = rows.map(({ f }) => f);
  const entryByKey = new Map(rows.map(({ f, key }) => [key, f]));
  const visibleKeys = new Set(rows.map(({ key }) => key));
  const selectedVisible = [...selected].filter((k) => visibleKeys.has(k));

  /** Resolve selected keys to handoff entries; null when a screen is gone. */
  function selectedEntries(): FindingEntry[] | null {
    const out: FindingEntry[] = [];
    for (const key of selectedVisible) {
      const f = entryByKey.get(key);
      if (!f) continue;
      const screen = screenById(f.screen_id);
      if (!screen) return null;
      out.push({ screen, finding: toFinding(f), repoFullName: f.repo_full_name });
    }
    return out;
  }

  const batchEntries = selectedVisible.length > 0 ? selectedEntries() : [];
  const batchGate = batchEntries && batchEntries.length > 0 ? gateBatch(batchEntries, repos) : null;

  function openBatchTask() {
    if (!batchEntries || !batchGate?.ok) return;
    sendToNewTask(navigate, batchEntries, batchGate);
    setSelected(new Set());
  }

  function markSelectedDealt() {
    const fps = selectedVisible.flatMap((key) => {
      const f = entryByKey.get(key);
      return f ? [fpOf(f)] : [];
    });
    if (fps.length === 0) return;
    const byScreen = groupFpsByScreen(fps);
    // Optimistic: hide now, clear selection; rollback + error on failure.
    setDealt((prev) => new Set([...prev, ...fps]));
    setSelected(new Set());
    setDealtError(null);
    Promise.all([...byScreen].map(([sid, list]) => api.markDealt(sid, list))).catch(() => {
      setDealt((prev) => {
        const next = new Set(prev);
        for (const fp of fps) next.delete(fp);
        return next;
      });
      setDealtError("Could not mark dealt — retry.");
    });
  }

  function toggleSelectVisible() {
    setSelected((prev) => {
      const allSelected = selectedVisible.length === rows.length && rows.length > 0;
      if (allSelected) {
        const next = new Set(prev);
        for (const k of selectedVisible) next.delete(k);
        return next;
      }
      return new Set([...prev, ...rows.map(({ key }) => key)]);
    });
  }

  if (loading) return <p className="text-sm text-ink-500">Loading findings…</p>;
  if (error) return <p className="text-sm text-red-400">Findings failed to load: {error}</p>;
  if (items.length === 0)
    return (
      <p className="text-sm text-ink-500">No findings in recent runs. Clean audits, quiet inbox.</p>
    );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {["all", "critical", "high", "medium", "low"].map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => setSeverity(s)}
            className={`rounded-full border px-2.5 py-1 text-xs ${
              severity === s
                ? "border-syrup-500/60 bg-syrup-500/10 text-syrup-300"
                : "border-ink-800 text-ink-400 hover:border-ink-600"
            }`}
          >
            {s}
          </button>
        ))}
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="filter title / file / screen…"
          className="field !w-56 !py-1 text-xs"
          aria-label="Filter findings"
        />
        <SearchableSelect
          label="Filter by screen"
          hideLabel
          value={screenFilter}
          onChange={(v) => setScreenFilter(v === "all" ? "all" : Number(v))}
          options={[
            { value: "all", label: "all screens" },
            ...screens.map((s) => ({
              value: String(s.id),
              label: qualifiedScreenName(s.name, repos.find((r) => r.id === s.repo_id)?.full_name),
            })),
          ]}
        />
        <button
          type="button"
          onClick={() => setHideDealt((v) => !v)}
          title={hideDealt ? "Currently hiding dealt findings" : "Currently showing dealt findings"}
          className={`rounded-full border px-2.5 py-1 text-xs ${
            hideDealt
              ? "border-syrup-500/60 bg-syrup-500/10 text-syrup-300"
              : "border-ink-800 text-ink-400 hover:border-ink-600"
          }`}
        >
          {hideDealt ? `Hide dealt${dealtCount > 0 ? ` (${dealtCount})` : ""}` : "Show dealt"}
        </button>
      </div>
      {rows.length > 0 && (
        <div
          className="flex flex-wrap items-center gap-2 rounded-lg border border-ink-800 px-3 py-2"
          role="region"
          aria-label="Batch finding actions"
        >
          <label className="flex cursor-pointer items-center gap-2 text-xs text-ink-400">
            <input
              type="checkbox"
              checked={selectedVisible.length === rows.length && rows.length > 0}
              ref={(el) => {
                if (el)
                  el.indeterminate =
                    selectedVisible.length > 0 && selectedVisible.length < rows.length;
              }}
              onChange={toggleSelectVisible}
              aria-label="Select all visible findings"
              className="h-3.5 w-3.5 accent-amber-500"
            />
            {selectedVisible.length > 0
              ? `${selectedVisible.length} selected`
              : `Select all (${rows.length})`}
          </label>
          {selectedVisible.length > 0 && (
            <>
              <button
                type="button"
                className="btn-ghost !px-2 !py-1 text-xs disabled:opacity-50"
                disabled={!batchGate?.ok}
                title={
                  batchGate?.ok
                    ? "Open the New-task form with one combined prompt"
                    : ((batchEntries === null
                        ? "A selected finding's screen is no longer loaded."
                        : batchGate?.reason) ?? undefined)
                }
                onClick={openBatchTask}
              >
                {selectedVisible.length === 1
                  ? "Open new task with finding"
                  : `Open new task with ${selectedVisible.length} findings`}
              </button>
              <button
                type="button"
                className="btn-ghost !px-2 !py-1 text-xs"
                onClick={markSelectedDealt}
              >
                Mark dealt ({selectedVisible.length})
              </button>
              <button
                type="button"
                className="btn-ghost !px-2 !py-1 text-xs"
                onClick={() => setSelected(new Set())}
              >
                Clear
              </button>
            </>
          )}
        </div>
      )}
      {dealtError && <p className="text-xs text-red-400">{dealtError}</p>}
      {batchEntries === null && selectedVisible.length > 0 && (
        <p className="text-xs text-amber-400">
          A selected finding&apos;s screen is no longer loaded — deselect it to create a task.
        </p>
      )}
      {batchGate && !batchGate.ok && <p className="text-xs text-amber-400">{batchGate.reason}</p>}
      {visible.length === 0 ? (
        <p className="text-sm text-ink-500">No findings match the filter.</p>
      ) : (
        <ul className="space-y-2">
          {rows.map(({ f, key }) => {
            const open = openKey === key;
            const screen = screenById(f.screen_id);
            const fp = fpOf(f);
            const isDealt = dealt.has(fp);
            const checked = selected.has(key);
            return (
              <li key={key} className="rounded-lg border border-ink-800 p-3">
                <div className="flex w-full flex-wrap items-center gap-2">
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleSelected(key)}
                    onClick={(e) => e.stopPropagation()}
                    aria-label={`Select finding ${f.title}`}
                    className="h-3.5 w-3.5 shrink-0 accent-amber-500"
                  />
                  <button
                    type="button"
                    onClick={() => setOpenKey(open ? null : key)}
                    className="flex min-w-0 flex-1 flex-wrap items-center gap-2 text-left"
                    aria-expanded={open}
                  >
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
                    <span className="ml-auto font-mono text-[11px] text-ink-600">
                      {qualifiedScreenName(f.screen_name, f.repo_full_name)} ·{" "}
                      {f.finished_at ? new Date(f.finished_at).toLocaleString() : "—"}
                    </span>
                    <span className="text-ink-600">{open ? "▾" : "▸"}</span>
                  </button>
                </div>
                {open && (
                  <div className="mt-2 space-y-1">
                    {f.detail && <p className="text-xs text-ink-400">{f.detail}</p>}
                    {f.recommendation && (
                      <p className="text-xs text-ink-500">
                        <span className="text-ink-400">Recommendation:</span> {f.recommendation}
                      </p>
                    )}
                    <span className="mt-1 flex w-fit gap-2">
                      {screen ? (
                        <button
                          type="button"
                          className="btn-ghost !px-2 !py-1 text-xs"
                          title="Open the New-task form with this finding's prompt injected"
                          onClick={() => {
                            const entry = {
                              screen,
                              finding: toFinding(f),
                              repoFullName: f.repo_full_name,
                            };
                            sendToNewTask(navigate, [entry], gateBatch([entry], repos));
                          }}
                        >
                          New task from finding
                        </button>
                      ) : (
                        <span className="text-xs text-amber-400">
                          Screen no longer loaded — cannot create a task.
                        </span>
                      )}
                      <button
                        type="button"
                        className="btn-ghost !px-2 !py-1 text-xs"
                        onClick={() => toggleDealt(fp, f.screen_id)}
                      >
                        {isDealt ? "Reopen" : "Mark dealt"}
                      </button>
                    </span>
                    <p className="text-[11px] text-ink-600">
                      The New-task form opens with the prompt filled in — review it there before
                      creating (findings are LLM-generated and may be wrong).
                    </p>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
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
  const [actionError, setActionError] = useState<string | null>(null);
  const repo = repos.find((r) => r.id === screen.repo_id);
  const latest = screen.latest_run ?? null;
  const inFlight = latest !== null && (latest.status === "running" || latest.status === "queued");

  async function runNow() {
    setRunning(true);
    setActionError(null);
    try {
      await api.runScreen(screen.id);
      // The run row is inserted by a background thread after the 200 — wait
      // for a new latest_run before refreshing, so the card doesn't sit on
      // stale state (bounded wait; refresh regardless on timeout).
      const prevId = screen.latest_run?.id ?? null;
      const deadline = Date.now() + 8000;
      for (;;) {
        await new Promise((r) => setTimeout(r, 1000));
        try {
          const screens = await api.getScreens();
          const fresh = screens.find((s) => s.id === screen.id);
          if (!fresh || (fresh.latest_run?.id ?? null) !== prevId) break;
        } catch {
          break;
        }
        if (Date.now() > deadline) break;
      }
      onEdited();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "failed to run");
    } finally {
      setRunning(false);
    }
  }

  async function remove() {
    if (!window.confirm(`Delete screen "${qualifiedScreenName(screen.name, repo?.full_name)}"?`))
      return;
    setActionError(null);
    try {
      await api.deleteScreen(screen.id);
      onDeleted();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "failed to delete");
    }
  }

  return (
    <div className="surface p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-ink-100">
            {qualifiedScreenName(screen.name, repo?.full_name)}
          </h3>
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

      {latest ? (
        <p className="mt-2 flex flex-wrap items-center gap-2 text-xs">
          <StatusPill status={latest.status} />
          {latest.status === "done" && (
            <span className="text-ink-400">
              {latest.finding_total === 0
                ? "clean"
                : `${latest.finding_total} finding${latest.finding_total > 1 ? "s" : ""}`}
            </span>
          )}
          {latest.status === "failed" && latest.error && (
            <span className="max-w-full truncate text-red-400" title={latest.error}>
              {latest.error}
            </span>
          )}
          {(latest.finished_at || latest.started_at) && (
            <span className="font-mono text-ink-600">
              {new Date((latest.finished_at || latest.started_at) as string).toLocaleString()}
            </span>
          )}
        </p>
      ) : (
        <p className="mt-2 text-xs text-ink-600">Never run.</p>
      )}

      {actionError && <p className="mt-2 text-xs text-red-400">{actionError}</p>}

      <div className="mt-4 flex items-center gap-2">
        <button
          type="button"
          onClick={runNow}
          disabled={running}
          className="btn-ghost !px-2.5 !py-1 text-xs disabled:opacity-50"
        >
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
          onClick={remove}
          disabled={inFlight}
          title={inFlight ? "Cannot delete while a run is in flight" : undefined}
          className="btn-ghost !px-2.5 !py-1 text-xs text-red-400 disabled:opacity-50"
        >
          Delete
        </button>
      </div>
    </div>
  );
}

export default function Screenings() {
  const location = useLocation();
  const fromMission = (location.state as { from?: string } | null)?.from === "mission";
  const [screens, setScreens] = useState<Screen[]>([]);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [templates, setTemplates] = useState<ScreenTemplate[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loadErrors, setLoadErrors] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Screen | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [openRuns, setOpenRuns] = useState<number | null>(null);
  const [view, setView] = useState<"screens" | "findings">("findings");

  const load = useCallback(() => {
    api
      .getScreens()
      .then((s) => {
        setScreens(s);
        setLoading(false);
      })
      .catch((e) => {
        setError(e.message);
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    load();
    api
      .getRepos()
      .then(setRepos)
      .catch((e) => setLoadErrors((p) => [...p, `repos: ${e.message}`]));
    api
      .getScreenTemplates()
      .then(setTemplates)
      .catch((e) => setLoadErrors((p) => [...p, `templates: ${e.message}`]));
    // Visiting the tab marks findings seen (clears the nav badge).
    try {
      localStorage.setItem(SCREENS_SEEN_KEY, String(Date.now()));
    } catch {
      // Private mode etc. — badge simply never clears.
    }
  }, [load]);

  return (
    <div className="space-y-6">
      {fromMission && (
        <div className="animate-fade-up">
          <Link
            to="/?view=mission"
            className="inline-flex items-center gap-1.5 text-sm text-ink-400 hover:text-syrup-300 transition-colors"
          >
            <span>←</span>
            <span>Back to Mission control</span>
          </Link>
        </div>
      )}
      <header className="flex items-start justify-between animate-fade-up">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-ink-100">Screenings</h1>
          <p className="mt-1 text-sm text-ink-500">
            Scheduled, read-only code audits. Screening finds and notifies — it never acts. Turn
            findings into tasks yourself. Audits run without your GitHub token — treat audited
            repositories as untrusted.
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
      {loadErrors.map((e) => (
        <p key={e} className="text-xs text-amber-400">
          {e}
        </p>
      ))}

      {!loading &&
        (() => {
          const failing = screens.filter((s) => s.latest_run?.status === "failed");
          const disabled = screens.filter((s) => !s.enabled);
          if (failing.length === 0 && disabled.length === 0) return null;
          return (
            <p className="surface animate-fade-up px-4 py-3 text-xs leading-relaxed">
              {failing.length > 0 && (
                <span className="text-red-400">
                  {failing.length} of {screens.length} screen{failing.length > 1 ? "s" : ""} failing
                  (
                  {failing
                    .map((s) =>
                      qualifiedScreenName(s.name, repos.find((r) => r.id === s.repo_id)?.full_name)
                    )
                    .join(", ")}
                  ) — open History for the error.{" "}
                </span>
              )}
              {disabled.length > 0 && (
                <span className="text-ink-500">
                  {disabled.length} disabled (
                  {disabled
                    .map((s) =>
                      qualifiedScreenName(s.name, repos.find((r) => r.id === s.repo_id)?.full_name)
                    )
                    .join(", ")}
                  ) — off-schedule until re-enabled.
                </span>
              )}
            </p>
          );
        })()}

      <div className="flex gap-2">
        {(["screens", "findings"] as const).map((v) => (
          <button
            key={v}
            type="button"
            onClick={() => setView(v)}
            className={`rounded-lg px-3 py-1.5 text-sm capitalize transition-colors ${
              view === v
                ? "bg-ink-850 text-ink-100 ring-1 ring-inset ring-ink-700"
                : "text-ink-400 hover:text-ink-100"
            }`}
          >
            {v}
          </button>
        ))}
      </div>

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

      {view === "findings" ? (
        <div className="animate-fade-up">
          <FindingsInbox screens={screens} repos={repos} />
        </div>
      ) : loading ? (
        <p className="text-sm text-ink-500 animate-fade-up">Loading screens…</p>
      ) : screens.length === 0 && !showForm ? (
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
                  <RunHistory screen={s} repos={repos} onChanged={load} />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
