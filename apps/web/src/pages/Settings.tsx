import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { SettingsMap } from "../types";

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

export default function Settings() {
  const [settings, setSettings] = useState<SettingsMap | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<Record<string, "saving" | "saved" | "error">>({});

  useEffect(() => {
    api
      .getSettings()
      .then(setSettings)
      .catch((e) => setError(e.message));
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
      label: "Default timeout",
      desc: "Minutes a task may run before it is force-killed.",
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
      key: "ntfy_topic",
      label: "ntfy topic",
      desc: "Push-notification topic (Phase 2 screening notifications).",
      control: (
        <input
          defaultValue={settings.ntfy_topic}
          onBlur={(e) => save("ntfy_topic", e.target.value.trim())}
          placeholder="my-jalebi"
          className="field max-w-xs font-mono"
        />
      ),
    },
    {
      key: "ntfy_url",
      label: "ntfy server URL",
      desc: "Base URL of the ntfy server (e.g. https://ntfy.sh). Leave blank for the default.",
      control: (
        <input
          defaultValue={settings.ntfy_url}
          onBlur={(e) => save("ntfy_url", e.target.value.trim())}
          placeholder="https://ntfy.sh"
          className="field max-w-xs font-mono"
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
