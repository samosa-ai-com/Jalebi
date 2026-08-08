import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { EnvVar, Repo, SettingsMap } from "../types";

const AGENT_CLIS = ["opencode"];

function Toggle({
  checked,
  onChange,
  ariaLabel,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  ariaLabel: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-label={ariaLabel}
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors duration-200 ${
        checked ? "bg-syrup-500" : "bg-ink-800 ring-1 ring-inset ring-ink-600"
      }`}
    >
      <span
        className={`inline-block h-[18px] w-[18px] rounded-full shadow-sm transition-transform duration-200 ${
          checked ? "translate-x-[23px] bg-ink-950" : "translate-x-[3px] bg-ink-300"
        }`}
      />
    </button>
  );
}

function EnvVarsSection({ repos }: { repos: Repo[] }) {
  const [vars, setVars] = useState<EnvVar[]>([]);
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [repoId, setRepoId] = useState<string>("");
  const [importText, setImportText] = useState("");
  const [importScope, setImportScope] = useState<string>("");
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  function reload() {
    api
      .getEnvVars()
      .then(setVars)
      .catch(() => {});
  }

  useEffect(reload, []);

  function repoLabel(id: number | null): string {
    if (id === null) return "global";
    return repos.find((r) => r.id === id)?.full_name ?? `repo#${id}`;
  }

  async function addVar(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !value) return;
    setBusy(true);
    setMsg(null);
    try {
      await api.upsertEnvVar(name.trim(), value, repoId ? Number(repoId) : null);
      setName("");
      setValue("");
      setMsg({ kind: "ok", text: "saved" });
      reload();
    } catch (err) {
      setMsg({ kind: "err", text: err instanceof Error ? err.message : "failed to save" });
    } finally {
      setBusy(false);
    }
  }

  async function importEnv(e: React.FormEvent) {
    e.preventDefault();
    if (!importText.trim()) return;
    setBusy(true);
    setMsg(null);
    try {
      const res = await api.importEnvVars(
        importText,
        importScope ? Number(importScope) : null
      );
      setImportText("");
      setMsg({ kind: "ok", text: `imported ${res.imported} variable(s)` });
      setVars(res.env_vars);
    } catch (err) {
      setMsg({ kind: "err", text: err instanceof Error ? err.message : "failed to import" });
    } finally {
      setBusy(false);
    }
  }

  async function removeVar(id: number) {
    try {
      await api.deleteEnvVar(id);
      setVars((prev) => prev.filter((v) => v.id !== id));
    } catch (err) {
      setMsg({ kind: "err", text: err instanceof Error ? err.message : "failed to delete" });
    }
  }

  return (
    <section className="surface p-5 animate-fade-up" style={{ animationDelay: "0.04s" }}>
      <h2 className="panel-title mb-1">Environment variables</h2>
      <p className="mb-4 text-xs leading-relaxed text-ink-500">
        Variables injected into task agents&apos; environments (build/test env, keys).
        Values are stored as secrets — never shown in full, and redacted if an agent
        echoes them. Pick which ones a task gets on the task form.
      </p>

      <form onSubmit={addVar} className="mb-4 grid gap-3 sm:grid-cols-4">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="NAME"
          className="field font-mono"
        />
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="value"
          type="password"
          autoComplete="off"
          className="field font-mono sm:col-span-2"
        />
        <div className="flex gap-2">
          <select
            value={repoId}
            onChange={(e) => setRepoId(e.target.value)}
            className="field flex-1"
            aria-label="Scope"
          >
            <option value="">global</option>
            {repos.map((r) => (
              <option key={r.id} value={r.id}>
                {r.full_name}
              </option>
            ))}
          </select>
          <button type="submit" disabled={busy || !name.trim() || !value} className="btn-primary">
            Add
          </button>
        </div>
      </form>

      <form onSubmit={importEnv} className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center">
        <textarea
          value={importText}
          onChange={(e) => setImportText(e.target.value)}
          rows={2}
          placeholder={"Paste a .env file…\nKEY=VALUE per line"}
          className="field flex-1 resize-y font-mono"
        />
        <select
          value={importScope}
          onChange={(e) => setImportScope(e.target.value)}
          className="field w-40"
          aria-label="Import scope"
        >
          <option value="">global</option>
          {repos.map((r) => (
            <option key={r.id} value={r.id}>
              {r.full_name}
            </option>
          ))}
        </select>
        <button type="submit" disabled={busy || !importText.trim()} className="btn-ghost">
          Import .env
        </button>
      </form>

      {msg && (
        <p className={`mb-3 text-xs ${msg.kind === "ok" ? "text-green-300" : "text-red-400"}`}>
          {msg.text}
        </p>
      )}

      {vars.length === 0 ? (
        <p className="text-xs text-ink-600">No environment variables configured yet.</p>
      ) : (
        <ul className="divide-y divide-ink-800/70">
          {vars.map((v) => (
            <li key={v.id} className="flex items-center gap-3 py-2">
              <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-chai-400" />
              <span className="font-mono text-sm text-ink-200">{v.name}</span>
              <span className="rounded bg-ink-800 px-1.5 py-0.5 font-mono text-[10px] text-ink-500">
                {repoLabel(v.repo_id)}
              </span>
              <span className="flex-1 truncate font-mono text-[11px] text-ink-600">
                {v.masked}
              </span>
              <button
                onClick={() => removeVar(v.id)}
                className="text-[11px] text-ink-500 transition-colors hover:text-red-400"
              >
                delete
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default function Settings() {
  const [settings, setSettings] = useState<SettingsMap | null>(null);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<Record<string, "saving" | "saved" | "error">>({});
  const [notifyTest, setNotifyTest] = useState<{ busy: boolean; result: string | null }>({
    busy: false,
    result: null,
  });

  useEffect(() => {
    api
      .getSettings()
      .then(setSettings)
      .catch((e) => setError(e.message));
    api
      .getRepos()
      .then(setRepos)
      .catch(() => {});
  }, []);

  async function save(key: string, value: unknown) {
    setStatus((s) => ({ ...s, [key]: "saving" }));
    try {
      await api.updateSetting(key, value);
      setError(null);
      setSettings((prev) => (prev ? { ...prev, [key]: value } : prev));
      setStatus((s) => ({ ...s, [key]: "saved" }));
      window.setTimeout(() => {
        setStatus((s) => {
          const next = { ...s };
          delete next[key];
          return next;
        });
      }, 2000);
    } catch (e) {
      setStatus((s) => ({ ...s, [key]: "error" }));
      setError(e instanceof Error ? e.message : "failed to save");
    }
  }

  async function sendTestNotification() {
    setNotifyTest({ busy: true, result: null });
    try {
      await api.testNotification();
      setNotifyTest({ busy: false, result: "sent" });
    } catch (e) {
      setNotifyTest({
        busy: false,
        result: e instanceof Error ? e.message : "notification failed",
      });
    }
  }

  if (error && !settings) return <p className="text-red-400">{error}</p>;
  if (!settings) return <p className="text-ink-500">Loading…</p>;

  function statusBadge(key: string) {
    const s = status[key];
    if (!s) return null;
    const color =
      s === "saving" ? "text-ink-500" : s === "saved" ? "text-green-300" : "text-red-400";
    return <span className={`font-mono text-[11px] ${color}`}>{s}</span>;
  }

  const rows = [
    {
      key: "concurrency",
      label: "Queue concurrency",
      desc: "Max tasks the agent pool runs in parallel (0 pauses the queue).",
      control: (
        <input
          type="number"
          min={0}
          max={64}
          defaultValue={settings.concurrency}
          onBlur={(e) => save("concurrency", Number(e.target.value))}
          className="field w-28 font-mono"
        />
      ),
    },
    {
      key: "auto_publish",
      label: "Auto-publish PRs",
      desc: "Open a pull request automatically when a task finishes. Off = hold for manual publish.",
      control: (
        <Toggle
          checked={settings.auto_publish}
          onChange={(v) => save("auto_publish", v)}
          ariaLabel="Auto-publish PRs"
        />
      ),
    },
    {
      key: "default_timeout_minutes",
      label: "Timeout",
      desc: "Default minutes a task may run before it is force-killed.",
      control: (
        <input
          type="number"
          min={1}
          defaultValue={settings.default_timeout_minutes}
          onBlur={(e) => save("default_timeout_minutes", Number(e.target.value))}
          className="field w-28 font-mono"
        />
      ),
    },
    {
      key: "agent_cli",
      label: "Agent backend",
      desc: "Which coding-agent CLI drives tasks. Switching is one line.",
      control: (
        <select
          value={settings.agent_cli}
          onChange={(e) => save("agent_cli", e.target.value)}
          className="field w-44"
        >
          {AGENT_CLIS.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      ),
    },
    {
      key: "retry_policy",
      label: "Auto-retry failures",
      desc: "Re-run a failed task once automatically.",
      control: (
        <Toggle
          checked={settings.retry_policy.auto_retry}
          onChange={(v) => save("retry_policy", { auto_retry: v })}
          ariaLabel="Auto-retry failures"
        />
      ),
    },
    {
      key: "artifact_ttl_days",
      label: "Artifact retention",
      desc: "Days to keep task artifacts before cleanup.",
      control: (
        <input
          type="number"
          min={1}
          defaultValue={settings.artifact_ttl_days}
          onBlur={(e) => save("artifact_ttl_days", Number(e.target.value))}
          className="field w-28 font-mono"
        />
      ),
    },
    {
      key: "secret_patterns",
      label: "Secret patterns",
      desc: "Regex patterns (one per line) redacted from agent output.",
      control: (
        <textarea
          rows={3}
          defaultValue={(settings.secret_patterns ?? []).join("\n")}
          onBlur={(e) =>
            save(
              "secret_patterns",
              e.target.value.split("\n").map((s) => s.trim()).filter(Boolean)
            )
          }
          placeholder={"AKIA[0-9A-Z]{16}\nsk-[A-Za-z0-9]{20,}"}
          className="field max-w-md resize-y font-mono"
        />
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <header className="animate-fade-up">
        <h1 className="text-3xl font-bold tracking-tight text-ink-100">Settings</h1>
        <p className="mt-1 text-sm text-ink-500">
          Runtime behaviour of the queue and the agent. Most changes apply immediately;
          artifact retention applies on the next start.
        </p>
      </header>

      {error && <p className="text-sm text-red-400">{error}</p>}

      <section className="surface p-5 animate-fade-up" style={{ animationDelay: "0.03s" }}>
        <h2 className="panel-title mb-1">Notifications</h2>
        <p className="mb-4 text-xs leading-relaxed text-ink-500">
          Push task lifecycle updates to an ntfy server. The endpoint is either a bare
          topic name (sent to <span className="font-mono">ntfy.sh</span>) or a full URL
          to a self-hosted server.
        </p>
        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-4">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-ink-400">
                ntfy endpoint
              </span>
              <input
                defaultValue={settings.ntfy_topic}
                onBlur={(e) => save("ntfy_topic", e.target.value.trim())}
                placeholder="my-jalebi"
                className="field max-w-xs font-mono"
              />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-ink-400">
                Progress ping interval (minutes)
              </span>
              <input
                type="number"
                min={1}
                defaultValue={settings.notify_progress_interval_minutes}
                onBlur={(e) =>
                  save("notify_progress_interval_minutes", Number(e.target.value))
                }
                className="field w-28 font-mono"
              />
            </label>
            <div className="flex items-center gap-3 pt-1">
              <button
                onClick={sendTestNotification}
                disabled={notifyTest.busy}
                className="btn-ghost !px-3 !py-1 text-xs disabled:opacity-40"
              >
                {notifyTest.busy ? "Sending…" : "Send test notification"}
              </button>
              {notifyTest.result === "sent" && (
                <span className="text-xs text-green-300">sent</span>
              )}
              {notifyTest.result && notifyTest.result !== "sent" && (
                <span className="text-xs text-red-400">{notifyTest.result}</span>
              )}
            </div>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="flex items-center justify-between gap-3 rounded border border-ink-800 px-3 py-2.5 text-sm">
              <span>Task done</span>
              <Toggle
                checked={settings.notify_on_done}
                onChange={(v) => save("notify_on_done", v)}
                ariaLabel="Notify on task done"
              />
            </label>
            <label className="flex items-center justify-between gap-3 rounded border border-ink-800 px-3 py-2.5 text-sm">
              <span>Task failed / timed out / cancelled</span>
              <Toggle
                checked={settings.notify_on_failed}
                onChange={(v) => save("notify_on_failed", v)}
                ariaLabel="Notify on task failure"
              />
            </label>
            <label className="flex items-center justify-between gap-3 rounded border border-ink-800 px-3 py-2.5 text-sm">
              <span>Still running (interval pings)</span>
              <Toggle
                checked={settings.notify_on_progress}
                onChange={(v) => save("notify_on_progress", v)}
                ariaLabel="Notify on progress"
              />
            </label>
            <label className="flex items-center justify-between gap-3 rounded border border-ink-800 px-3 py-2.5 text-sm">
              <span>Needs approval</span>
              <Toggle
                checked={settings.notify_on_needs_approval}
                onChange={(v) => save("notify_on_needs_approval", v)}
                ariaLabel="Notify on needs approval"
              />
            </label>
          </div>
        </div>
      </section>

      <EnvVarsSection repos={repos} />

      <div className="grid gap-4 md:grid-cols-2 animate-fade-up" style={{ animationDelay: "0.05s" }}>
        {rows.map((row) => (
          <section key={row.key} className="surface flex flex-col justify-between gap-4 p-5">
            <div>
              <div className="flex items-center gap-2">
                <h2 className="panel-title">{row.label}</h2>
                {statusBadge(row.key)}
              </div>
              <p className="mt-1 text-xs leading-relaxed text-ink-500">{row.desc}</p>
            </div>
            <div className="flex items-center justify-end">{row.control}</div>
          </section>
        ))}
      </div>
    </div>
  );
}
