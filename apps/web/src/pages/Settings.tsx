import { Children, isValidElement, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type {
  BackupInfo,
  DataUsage,
  DetectedIde,
  EnvVar,
  PrunePreview,
  Repo,
  SettingsMap,
} from "../types";

function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let u = 0;
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024;
    u += 1;
  }
  return `${v.toFixed(v >= 100 ? 0 : 1)} ${units[u]}`;
}

// Collapsible section wrapper. A <div> (not <section>) so each inner row
// keeps its own <section> landmark for headings/tests. Everything starts
// collapsed; a search query filters rows, hides empty sections, and forces
// matches open (the toggle state is restored when the query clears).
function Section({
  id,
  title,
  desc,
  children,
  query = "",
  keywords = "",
  onVisibility,
}: {
  id: string;
  title: string;
  desc?: string;
  children: React.ReactNode;
  query?: string;
  keywords?: string;
  onVisibility?: (id: string, visible: boolean) => void;
}) {
  const [open, setOpen] = useState(() => {
    try {
      return localStorage.getItem(`jalebi-settings-open-v2:${id}`) === "1";
    } catch {
      return false; // storage unavailable — stay collapsed
    }
  });
  const toggle = () => {
    setOpen((v) => {
      try {
        localStorage.setItem(`jalebi-settings-open-v2:${id}`, v ? "0" : "1");
      } catch {
        // ignore storage failures
      }
      return !v;
    });
  };
  const q = query.trim().toLowerCase();
  // Atomic sections (keywords set — custom JSX, no filterable rows) live or
  // die on the section-level match. Row sections narrow to matching rows,
  // unless the title itself matches (then the whole section shows).
  const sectionMatch = q !== "" && matchesQuery(q, title, desc, keywords);
  const content =
    q === "" || sectionMatch ? children : keywords !== "" ? null : filterTree(children, q);
  const visible = content !== null && treeHasContent(content);
  useEffect(() => {
    onVisibility?.(id, visible);
  }, [id, visible, onVisibility]);
  if (!visible) return null;
  const shown = q === "" ? open : true;
  return (
    <div className="surface p-5 animate-fade-up">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={shown}
        className="flex w-full items-center justify-between gap-2 text-left"
      >
        <h2 className="panel-title">{title}</h2>
        <span className="text-ink-500 font-mono text-sm" aria-hidden>
          {shown ? "▾" : "▸"}
        </span>
      </button>
      {desc && <p className="mt-1 text-xs leading-relaxed text-ink-500">{desc}</p>}
      {shown && <div className="mt-4">{content}</div>}
    </div>
  );
}

function matchesQuery(q: string, ...texts: (string | undefined)[]): boolean {
  return texts.some((t) => (t ?? "").toLowerCase().includes(q));
}

function isRowElement(node: React.ReactNode): boolean {
  if (!isValidElement(node)) return false;
  const props = node.props as Record<string, unknown>;
  return typeof props.label === "string" && typeof props.desc === "string";
}

function rowMatches(node: React.ReactNode, q: string): boolean {
  if (!isValidElement(node)) return false;
  const props = node.props as Record<string, unknown>;
  return matchesQuery(q, props.label as string, props.desc as string);
}

// Recursively drop non-matching Rows (and containers left without content).
function filterTree(node: React.ReactNode, q: string): React.ReactNode {
  const out = Children.map(node, (child) => {
    if (!isValidElement(child)) return child;
    if (isRowElement(child)) return rowMatches(child, q) ? child : null;
    const props = child.props as { children?: React.ReactNode };
    if (props.children === undefined) return child;
    const filtered = filterTree(props.children, q);
    if (!treeHasContent(filtered)) return null;
    return filtered === props.children
      ? child
      : { ...child, props: { ...props, children: filtered } };
  });
  return out;
}

// Whether a (possibly filtered) subtree holds any renderable content:
// a Row, a non-empty string, or a container that still holds content.
function treeHasContent(node: React.ReactNode): boolean {
  let found = false;
  Children.forEach(node, (child) => {
    if (found) return;
    if (child === null || child === undefined || child === false) return;
    if (typeof child === "string") {
      if (child.trim() !== "") found = true;
      return;
    }
    if (typeof child === "number") {
      found = true;
      return;
    }
    if (isRowElement(child)) {
      found = true;
      return;
    }
    if (isValidElement(child)) {
      const props = child.props as { children?: React.ReactNode };
      if (props.children !== undefined && treeHasContent(props.children)) found = true;
      else if (props.children === undefined) found = true; // leaf widget (input/select)
    }
  });
  return found;
}

function Row({
  label,
  desc,
  status,
  error,
  children,
}: {
  label: string;
  desc: string;
  status?: React.ReactNode;
  error?: string | null;
  children: React.ReactNode;
}) {
  return (
    <section className="surface flex flex-col justify-between gap-4 p-5">
      <div>
        <div className="flex items-center gap-2">
          <h2 className="panel-title">{label}</h2>
          {status}
        </div>
        <p className="mt-1 text-xs leading-relaxed text-ink-500">{desc}</p>
      </div>
      <div className="flex items-center justify-end">{children}</div>
      {error && <p className="text-xs text-red-400">{error}</p>}
    </section>
  );
}

// Phase 4 T6 — IDE connector settings.
function IDESettings({
  query,
  onVisibility,
  resetKey,
}: {
  query: string;
  onVisibility: (id: string, visible: boolean) => void;
  resetKey: number;
}) {
  const [command, setCommand] = useState("");
  const [draftCommand, setDraftCommand] = useState("");
  const [name, setName] = useState("");
  const [draftName, setDraftName] = useState("");
  const [found, setFound] = useState(false);
  const [detected, setDetected] = useState<DetectedIde[]>([]);
  const [isCustom, setIsCustom] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [testMsg, setTestMsg] = useState<string | null>(null);
  const [testErr, setTestErr] = useState<string | null>(null);

  const loadStatus = () => {
    api
      .getIdeStatus()
      .then((s) => {
        setCommand(s.command);
        setDraftCommand(s.command);
        setName(s.name);
        setDraftName(s.name);
        setFound(s.found);
      })
      .catch(() => {});
  };

  const loadDetected = () => {
    api
      .detectIde()
      .then((d) => {
        const list = d.detected ?? (d.command ? [{ command: d.command, name: d.name }] : []);
        setDetected(list);
      })
      .catch(() => {});
  };

  useEffect(() => {
    loadStatus();
    loadDetected();
  }, []);

  const selectIde = (ide: DetectedIde) => {
    setCommand(ide.command);
    setDraftCommand(ide.command);
    setName(ide.name);
    setDraftName(ide.name);
    setFound(true);
    setIsCustom(false);
    setTestMsg(null);
    setTestErr(null);
    Promise.all([
      api.updateSetting("ide_command", ide.command),
      api.updateSetting("ide_name", ide.name),
    ])
      .then(() => setStatus(`saved (${ide.name})`))
      .catch((e) => setStatus(e instanceof Error ? e.message : "save failed"));
  };

  const commitCommand = (v: string) => {
    const trimmed = v.trim();
    setDraftCommand(v);
    if (trimmed === command) return;
    setCommand(trimmed);
    api
      .updateSetting("ide_command", trimmed)
      .then(() => {
        setStatus("saved");
        api
          .getIdeStatus()
          .then((s) => setFound(s.found))
          .catch(() => {});
      })
      .catch((e) => {
        setStatus(e instanceof Error ? e.message : "save failed");
        setDraftCommand(command);
      });
  };

  const commitName = (v: string) => {
    const trimmed = v.trim();
    setDraftName(v);
    if (trimmed === name) return;
    setName(trimmed);
    api
      .updateSetting("ide_name", trimmed)
      .then(() => setStatus("saved"))
      .catch((e) => {
        setStatus(e instanceof Error ? e.message : "save failed");
        setDraftName(name);
      });
  };

  const clearIde = () => {
    setCommand("");
    setDraftCommand("");
    setName("");
    setDraftName("");
    setFound(false);
    setIsCustom(false);
    setTestMsg(null);
    setTestErr(null);
    Promise.all([api.updateSetting("ide_command", ""), api.updateSetting("ide_name", "")])
      .then(() => setStatus("IDE disabled"))
      .catch((e) => setStatus(e instanceof Error ? e.message : "failed to clear"));
  };

  const test = () => {
    setTestMsg(null);
    setTestErr(null);
    api
      .testIde()
      .then((r) => setTestMsg(r.ok ? "launched on a scratch dir" : (r.error ?? "failed")))
      .catch((e) => setTestErr(e instanceof Error ? e.message : "test failed"));
  };

  const matchedDetected = detected.find((d) => d.command === command);
  const showCustomInputs = isCustom || !matchedDetected;

  return (
    <Section
      key={`ide-${resetKey}`}
      id="ide"
      title="IDE"
      desc="Configure an IDE binary so you can open a task's worktree directly from the task detail page and file browser."
      query={query}
      keywords="ide editor open worktree cursor vscode"
      onVisibility={onVisibility}
    >
      <div className="flex flex-wrap items-center justify-between gap-2 mb-1">
        {command ? (
          <span
            className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-medium ${
              found
                ? "bg-green-500/10 text-green-300 ring-1 ring-green-500/30"
                : "bg-red-500/10 text-red-300 ring-1 ring-red-500/30"
            }`}
          >
            <span className={`h-1.5 w-1.5 rounded-full ${found ? "bg-green-400" : "bg-red-400"}`} />
            {name || command} {found ? "ready" : "not found on PATH"}
          </span>
        ) : (
          <span className="text-[11px] text-ink-600">No IDE configured.</span>
        )}
      </div>

      {/* Detected IDEs */}
      <div className="mb-4 space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium text-ink-400">Detected IDEs on your system</span>
          <button
            type="button"
            onClick={loadDetected}
            className="text-[11px] text-ink-500 hover:text-ink-300 underline-offset-2 hover:underline"
          >
            Scan again
          </button>
        </div>

        {detected.length > 0 ? (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 md:grid-cols-3">
            {detected.map((d) => {
              const active = command === d.command;
              return (
                <button
                  key={d.command}
                  type="button"
                  onClick={() => selectIde(d)}
                  className={`flex flex-col items-start rounded-lg border p-3 text-left transition-all ${
                    active
                      ? "border-syrup-500 bg-syrup-500/10 ring-1 ring-syrup-500"
                      : "border-ink-800 bg-ink-900/40 hover:border-ink-700 hover:bg-ink-850"
                  }`}
                >
                  <div className="flex w-full items-center justify-between">
                    <span className="text-sm font-semibold text-ink-100">{d.name}</span>
                    {active && <span className="text-xs text-syrup-400 font-mono">✓ active</span>}
                  </div>
                  <span
                    className="mt-0.5 font-mono text-[11px] text-ink-500 truncate w-full"
                    title={d.path ?? d.command}
                  >
                    {d.command} {d.path ? `· ${d.path}` : ""}
                  </span>
                </button>
              );
            })}
            <button
              type="button"
              onClick={() => {
                setIsCustom(true);
              }}
              className={`flex flex-col items-start rounded-lg border p-3 text-left transition-all ${
                showCustomInputs
                  ? "border-syrup-500 bg-syrup-500/10 ring-1 ring-syrup-500"
                  : "border-dashed border-ink-800 bg-ink-900/20 hover:border-ink-700 hover:bg-ink-850"
              }`}
            >
              <span className="text-sm font-semibold text-ink-300">Custom command…</span>
              <span className="mt-0.5 text-[11px] text-ink-600">
                Enter custom CLI binary or path
              </span>
            </button>
          </div>
        ) : (
          <div className="space-y-2">
            <p className="text-xs text-ink-500">No common IDEs automatically found on PATH.</p>
            <button
              type="button"
              onClick={() => {
                setIsCustom(true);
              }}
              className="flex flex-col items-start rounded-lg border border-dashed border-ink-800 bg-ink-900/20 hover:border-ink-700 hover:bg-ink-850 p-3 text-left transition-all"
            >
              <span className="text-sm font-semibold text-ink-300">Custom command…</span>
              <span className="mt-0.5 text-[11px] text-ink-600">
                Enter custom CLI binary or path
              </span>
            </button>
          </div>
        )}
      </div>

      {/* Custom command input fields */}
      {showCustomInputs && (
        <div className="mb-4 rounded-lg border border-ink-800 bg-ink-900/50 p-4">
          <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-ink-400">
            Custom IDE Command
          </h3>
          <div className="grid gap-4 md:grid-cols-2">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-ink-400">
                CLI Command / Executable Path
                {draftCommand && (
                  <span className={`ml-2 ${found ? "text-green-300" : "text-red-300"}`}>
                    {found ? "found ✓" : "not found ✕"}
                  </span>
                )}
              </span>
              <input
                value={draftCommand}
                onChange={(e) => setDraftCommand(e.target.value)}
                onBlur={(e) => commitCommand(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                }}
                placeholder="e.g. cursor, code, nvim, /usr/bin/zed"
                className="field w-full font-mono text-xs"
              />
            </label>
            <div>
              <span className="mb-1.5 block text-xs font-medium text-ink-400">Display Name</span>
              <input
                value={draftName}
                onChange={(e) => setDraftName(e.target.value)}
                onBlur={(e) => commitName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                }}
                placeholder="e.g. My Editor"
                className="field w-full text-xs"
              />
            </div>
          </div>
        </div>
      )}

      {/* Actions and Status */}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={test}
          disabled={!command}
          className="btn-ghost text-xs disabled:opacity-40"
        >
          Test open
        </button>
        {command && (
          <button
            type="button"
            onClick={clearIde}
            className="btn-ghost text-xs text-ink-500 hover:text-red-400"
          >
            Disable IDE
          </button>
        )}
        {status && <span className="text-xs text-ink-500">{status}</span>}
        {testMsg && <span className="text-xs text-green-300">{testMsg}</span>}
        {testErr && <span className="text-xs text-red-400">{testErr}</span>}
      </div>
    </Section>
  );
}

const AGENT_CLIS = ["opencode", "codex", "claude"];

const SECTION_IDS = [
  "agent",
  "queue",
  "recovery",
  "notifications",
  "webhooks",
  "ide",
  "envvars",
  "data",
];

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

/** Controlled text input: edits stay local, commit on blur/Enter.
 * A failed save reverts the draft so the UI never shows an unsaved value. */
function TextInput({
  value,
  onCommit,
  className,
  placeholder,
  ariaLabel,
  mono,
}: {
  value: string;
  onCommit: (v: string) => Promise<boolean> | boolean | void;
  className?: string;
  placeholder?: string;
  ariaLabel?: string;
  mono?: boolean;
}) {
  const [draft, setDraft] = useState(value);
  // Reset the draft when the saved value changes elsewhere (the documented
  // render-time adjustment pattern — no effect, no cascading renders).
  const [lastValue, setLastValue] = useState(value);
  if (lastValue !== value) {
    setLastValue(value);
    setDraft(value);
  }
  const commit = (next: string) => {
    if (next === value) {
      setDraft(value);
      return;
    }
    void (async () => {
      const ok = await onCommit(next);
      if (ok === false) setDraft(value);
    })();
  };
  return (
    <input
      type="text"
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={(e) => commit(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "Escape") setDraft(value);
      }}
      placeholder={placeholder}
      aria-label={ariaLabel}
      className={`${className ?? "field"}${mono ? " font-mono" : ""}`}
    />
  );
}

/** Controlled integer input: empty/invalid edits revert, never POST NaN.
 * A failed save reverts the draft so the UI never shows an unsaved value. */
function NumberInput({
  value,
  onCommit,
  className,
  min,
  max,
  ariaLabel,
}: {
  value: number;
  onCommit: (v: number) => Promise<boolean> | boolean | void;
  className?: string;
  min?: number;
  max?: number;
  ariaLabel?: string;
}) {
  const [draft, setDraft] = useState(String(value));
  const [lastValue, setLastValue] = useState(value);
  if (lastValue !== value) {
    setLastValue(value);
    setDraft(String(value));
  }
  const commit = () => {
    const n = Number(draft);
    if (!Number.isInteger(n)) {
      setDraft(String(value));
      return;
    }
    let v = n;
    if (min !== undefined) v = Math.max(min, v);
    if (max !== undefined) v = Math.min(max, v);
    if (v !== value) {
      const current = value;
      setDraft(String(v));
      void (async () => {
        const ok = await onCommit(v);
        if (ok === false) setDraft(String(current));
      })();
    } else {
      setDraft(String(value));
    }
  };
  return (
    <input
      type="number"
      value={draft}
      min={min}
      max={max}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "Escape") setDraft(String(value));
      }}
      aria-label={ariaLabel}
      className={`${className ?? "field w-28"} font-mono`}
    />
  );
}

const ENV_NAME_RE = /^[A-Z_][A-Z0-9_]*$/;

function EnvVarsSection({
  repos,
  reposLoaded,
  query,
  onVisibility,
  resetKey,
}: {
  repos: Repo[];
  reposLoaded: boolean;
  query: string;
  onVisibility: (id: string, visible: boolean) => void;
  resetKey: number;
}) {
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

  const nameError =
    name.trim() && !ENV_NAME_RE.test(name.trim())
      ? "Use UPPER_SNAKE_CASE (letters, digits, underscore; must not start with a digit)."
      : null;

  async function addVar(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !value || nameError) return;
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
      const res = await api.importEnvVars(importText, importScope ? Number(importScope) : null);
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
    <Section
      key={`envvars-${resetKey}`}
      id="envvars"
      title="Environment variables"
      desc="Variables injected into task agents' environments (build/test env, keys). Values are stored as secrets — never shown in full, and redacted if an agent echoes them. Pick which ones a task gets on the task form."
      query={query}
      keywords="environment variables env secrets keys dotenv"
      onVisibility={onVisibility}
    >
      <form onSubmit={addVar} className="mb-4 grid gap-3 sm:grid-cols-4">
        <div>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="NAME"
            aria-label="Variable name"
            className="field font-mono"
          />
          {nameError && <p className="mt-1 text-[11px] text-red-400">{nameError}</p>}
        </div>
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="value"
          type="password"
          autoComplete="off"
          aria-label="Variable value"
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
          <button
            type="submit"
            disabled={busy || !name.trim() || !value || !!nameError}
            className="btn-primary"
          >
            Add
          </button>
        </div>
      </form>
      {!reposLoaded && (
        <p className="mb-2 text-[11px] text-ink-600">
          Loading repositories… per-repo scopes appear when ready.
        </p>
      )}

      <form onSubmit={importEnv} className="mb-4 space-y-2">
        <textarea
          value={importText}
          onChange={(e) => setImportText(e.target.value)}
          rows={4}
          placeholder={"Paste a .env file…\nKEY=VALUE per line"}
          aria-label="Paste a .env file"
          className="field w-full resize-y font-mono leading-relaxed"
        />
        <div className="flex flex-wrap items-center gap-2">
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
        </div>
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
              <span className="flex-1 truncate font-mono text-[11px] text-ink-600">{v.masked}</span>
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
    </Section>
  );
}

const PRUNE_SCOPES = [
  { key: "tasks", label: "Old tasks", desc: "terminal tasks + runs, diffs, artifacts, worktrees" },
  { key: "orphans", label: "Orphans", desc: "worktree/artifact dirs with no DB row" },
  { key: "deliveries", label: "Deliveries", desc: "old webhook deliveries + screening runs" },
  { key: "logs", label: "Logs", desc: "log files older than the cutoff" },
];

function DataSection({
  query,
  onVisibility,
  resetKey,
}: {
  query: string;
  onVisibility: (id: string, visible: boolean) => void;
  resetKey: number;
}) {
  const [usage, setUsage] = useState<DataUsage | null>(null);
  const [usageErr, setUsageErr] = useState<string | null>(null);
  const [backups, setBackups] = useState<BackupInfo[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [days, setDays] = useState("30");
  const [scopes, setScopes] = useState<string[]>(PRUNE_SCOPES.map((s) => s.key));
  const [preview, setPreview] = useState<PrunePreview | null>(null);
  const [confirm, setConfirm] = useState("");

  function reload() {
    api
      .getDataUsage()
      .then((u) => {
        setUsage(u && typeof u === "object" && u.sizes ? u : null);
        setUsageErr(null);
      })
      .catch((e) => setUsageErr(e instanceof Error ? e.message : "failed to load"));
    api
      .getBackups()
      .then((b) => setBackups(Array.isArray(b) ? b : []))
      .catch(() => {});
  }

  useEffect(reload, []);

  async function runBusy(key: string, fn: () => Promise<string>) {
    setBusy(key);
    setMsg(null);
    try {
      setMsg({ kind: "ok", text: await fn() });
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof Error ? e.message : "failed" });
    } finally {
      setBusy(null);
      reload();
    }
  }

  const toggleScope = (key: string) =>
    setScopes((prev) => {
      setPreview(null);
      setConfirm("");
      return prev.includes(key) ? prev.filter((s) => s !== key) : [...prev, key];
    });

  async function runPreview() {
    const n = Number(days);
    if (!Number.isInteger(n) || n < 1 || scopes.length === 0) return;
    await runBusy("preview", async () => {
      const res = await api.pruneData({ older_than_days: n, scopes, dry_run: true });
      setPreview(res.preview);
      const c = res.preview.tasks.count;
      return `preview: ${c} task(s), ${res.preview.orphan_worktrees.length} orphan worktree(s), ${res.preview.deliveries} deliveries would be removed`;
    });
  }

  async function runPrune() {
    const n = Number(days);
    if (confirm !== "DELETE") return;
    await runBusy("prune", async () => {
      const res = await api.pruneData({
        older_than_days: n,
        scopes,
        dry_run: false,
        confirm: "DELETE",
      });
      setPreview(res.preview);
      setConfirm("");
      const r = res.removed ?? {};
      return `removed ${r.tasks ?? 0} task(s), ${r.orphan_worktrees ?? 0} orphan(s), ${r.deliveries ?? 0} deliveries`;
    });
  }

  const sizeEntries: [string, string][] = [
    ["sizes.db", "Database"],
    ["sizes.artifacts", "Artifacts"],
    ["sizes.mirrors", "Git mirrors"],
    ["sizes.worktrees", "Worktrees"],
    ["sizes.logs", "Logs"],
    ["sizes.backups", "Backups"],
  ];
  const totalSize = usage
    ? ["db", "artifacts", "mirrors", "worktrees", "logs", "backups"].reduce(
        (a, k) => a + (usage.sizes?.[k] ?? 0),
        0
      )
    : 0;

  return (
    <Section
      key={`data-${resetKey}`}
      id="data"
      title="Data management"
      desc="Back up the database, reclaim disk, and see where every byte lives. Prune always previews first and never touches queued, running, or blocked tasks."
      query={query}
      keywords="data backup prune vacuum storage cleanup disk usage"
      onVisibility={onVisibility}
    >
      {usageErr && <p className="mb-3 text-xs text-red-400">{usageErr}</p>}
      {/* Storage meter */}
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-400">
        Storage {totalSize > 0 && <span className="text-ink-600">· {formatBytes(totalSize)}</span>}
      </h3>
      {usage ? (
        <div className="mb-5 space-y-1.5">
          {sizeEntries.map(([path, label]) => {
            const key = path.split(".")[1];
            const v = usage.sizes?.[key] ?? 0;
            const pct = totalSize > 0 ? Math.max(1, Math.round((v / totalSize) * 100)) : 0;
            return (
              <div key={key} className="flex items-center gap-2 text-xs">
                <span className="w-24 shrink-0 text-ink-400">{label}</span>
                <div className="h-2 flex-1 overflow-hidden rounded bg-ink-800">
                  <div className="h-full rounded bg-syrup-500/70" style={{ width: `${pct}%` }} />
                </div>
                <span className="w-20 shrink-0 text-right font-mono text-ink-500">
                  {formatBytes(v)}
                </span>
              </div>
            );
          })}
          <p className="pt-1 text-[11px] text-ink-600">
            {usage.counts?.tasks ?? 0} tasks · {usage.counts?.runs ?? 0} runs ·{" "}
            {usage.counts?.artifacts ?? 0} artifacts · {usage.counts?.task_events ?? 0} timeline
            events
          </p>
        </div>
      ) : (
        <p className="mb-5 text-xs text-ink-600">Loading usage…</p>
      )}

      {/* Backups */}
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-400">Backups</h3>
      <p className="mb-2 text-[11px] leading-relaxed text-ink-600">
        Consistent snapshot of the live database (safe while running). Restore: stop the server,
        replace <span className="font-mono">data.db</span> with the backup, start again.
      </p>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() =>
            runBusy("backup", async () => {
              const b = await api.createBackup();
              return `backed up as ${b.name} (${formatBytes(b.size)})`;
            })
          }
          disabled={busy !== null}
          className="btn-ghost text-xs disabled:opacity-40"
        >
          {busy === "backup" ? "Backing up…" : "Back up now"}
        </button>
        <button
          type="button"
          onClick={() =>
            runBusy("vacuum", async () => {
              const r = await api.vacuumData();
              return `vacuumed ${formatBytes(r.before)} → ${formatBytes(r.after)}`;
            })
          }
          disabled={busy !== null}
          className="btn-ghost text-xs disabled:opacity-40"
          title="Rebuilds the database file; briefly locks the database while running."
        >
          {busy === "vacuum" ? "Vacuuming…" : "Vacuum database"}
        </button>
      </div>
      {backups.length === 0 ? (
        <p className="mb-5 text-xs text-ink-600">No backups yet.</p>
      ) : (
        <ul className="mb-5 divide-y divide-ink-800/70">
          {backups.map((b) => (
            <li key={b.name} className="flex items-center gap-3 py-2 text-xs">
              <span className="font-mono text-ink-200">{b.name}</span>
              <span className="font-mono text-ink-600">{formatBytes(b.size)}</span>
              <span className="flex-1" />
              <a
                href={api.backupDownloadUrl(b.name)}
                download={b.name}
                className="text-ink-400 hover:text-ink-200 underline-offset-2 hover:underline"
              >
                download
              </a>
              <button
                type="button"
                onClick={() =>
                  runBusy(`del-${b.name}`, async () => {
                    await api.deleteBackup(b.name);
                    return `deleted ${b.name}`;
                  })
                }
                className="text-ink-500 hover:text-red-400"
              >
                delete
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* Prune */}
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-400">
        Clean up old data
      </h3>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2 text-xs text-ink-400">
          Older than
          <input
            type="number"
            min={1}
            value={days}
            onChange={(e) => {
              setDays(e.target.value);
              setPreview(null);
              setConfirm("");
            }}
            className="field w-20 font-mono"
            aria-label="Older than (days)"
          />
          days
        </label>
        {PRUNE_SCOPES.map((s) => (
          <label
            key={s.key}
            title={s.desc}
            className="flex cursor-pointer items-center gap-1.5 rounded border border-ink-800 px-2 py-1 text-[11px] text-ink-400"
          >
            <input
              type="checkbox"
              checked={scopes.includes(s.key)}
              onChange={() => toggleScope(s.key)}
              className="accent-amber-500"
            />
            {s.label}
          </label>
        ))}
        <button
          type="button"
          onClick={runPreview}
          disabled={busy !== null || scopes.length === 0}
          className="btn-ghost text-xs disabled:opacity-40"
        >
          {busy === "preview" ? "Previewing…" : "Preview"}
        </button>
      </div>
      {preview && (
        <div className="mb-3 rounded-lg border border-ink-800 bg-ink-900/50 p-3 text-xs text-ink-400">
          <p className="font-mono text-[11px] text-ink-500">cutoff: {preview.cutoff}</p>
          <ul className="mt-1 list-disc pl-5">
            <li>
              {preview.tasks.count} task(s), {preview.runs} run(s), {preview.task_events} timeline
              event(s)
            </li>
            <li>
              {preview.orphan_worktrees.length} orphan worktree(s),{" "}
              {preview.orphan_artifacts.length} orphan artifact dir(s)
            </li>
            <li>
              {preview.deliveries} webhook deliverie(s), {preview.screening_runs} old screening
              run(s), {preview.old_logs} log file(s)
            </li>
          </ul>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <input
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              placeholder="type DELETE to confirm"
              aria-label="Type DELETE to confirm prune"
              className="field w-48 font-mono text-xs"
            />
            <button
              type="button"
              onClick={runPrune}
              disabled={busy !== null || confirm !== "DELETE"}
              className="btn-ghost text-xs text-red-300 disabled:opacity-40"
            >
              {busy === "prune" ? "Pruning…" : "Prune now"}
            </button>
          </div>
        </div>
      )}
      {msg && (
        <p className={`text-xs ${msg.kind === "ok" ? "text-green-300" : "text-red-400"}`}>
          {msg.text}
        </p>
      )}
    </Section>
  );
}

export default function Settings() {
  const [settings, setSettings] = useState<SettingsMap | null>(null);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [reposLoaded, setReposLoaded] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [fieldState, setFieldState] = useState<
    Record<string, { state: "saving" | "saved" | "error"; msg?: string }>
  >({});
  const timers = useRef<Record<string, number>>({});

  useEffect(() => {
    const pending = timers.current;
    return () => {
      Object.values(pending).forEach((t) => window.clearTimeout(t));
    };
  }, []);

  // Model options for the Default model dropdown — follow the Default backend.
  const [defaultModels, setDefaultModels] = useState<string[]>([]);
  // Settings search: filters rows, hides empty sections, forces matches open.
  const [query, setQuery] = useState("");
  const [visibleSections, setVisibleSections] = useState<Record<string, boolean>>({});
  function handleVisibility(id: string, visible: boolean) {
    setVisibleSections((prev) => (prev[id] === visible ? prev : { ...prev, [id]: visible }));
  }
  // Bulk expand/collapse: persist every section, then remount sections so
  // they pick up the stored values (no effect-setState, lint-safe).
  const [bulkN, setBulkN] = useState(0);
  function setAllSections(open: boolean) {
    try {
      SECTION_IDS.forEach((id) =>
        localStorage.setItem(`jalebi-settings-open-v2:${id}`, open ? "1" : "0")
      );
    } catch {
      // ignore storage failures
    }
    setBulkN((n) => n + 1);
  }

  useEffect(() => {
    if (!settings?.default_backend) return;
    let cancelled = false;
    api
      .getModels(settings.default_backend)
      .then((m) => {
        if (!cancelled) setDefaultModels(m.models ?? []);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [settings?.default_backend]);

  useEffect(() => {
    api
      .getSettings()
      .then((s) => {
        // Backfill newer keys for DBs seeded before they existed.
        const rp = s.retry_policy ?? {};
        setSettings({
          ...s,
          auto_nudge: s.auto_nudge ?? false,
          adapter_model_lists: s.adapter_model_lists ?? {},
          retry_policy: {
            auto_retry: rp.auto_retry ?? false,
            continue_prompt: rp.continue_prompt ?? "continue",
            timeout_multiplier: rp.timeout_multiplier ?? 2,
            max_timeout_minutes: rp.max_timeout_minutes ?? 180,
            max_attempts: rp.max_attempts ?? 3,
            non_retryable_patterns: rp.non_retryable_patterns ?? [],
          },
        });
      })
      .catch((e) => setLoadError(e.message));
    api
      .getRepos()
      .then((r) => {
        setRepos(r);
        setReposLoaded(true);
      })
      .catch(() => {
        setReposLoaded(true);
      });
  }, []);

  function mark(key: string, entry: { state: "saving" | "saved" | "error"; msg?: string }) {
    setFieldState((s) => ({ ...s, [key]: entry }));
    if (timers.current[key]) window.clearTimeout(timers.current[key]);
    if (entry.state === "saved") {
      timers.current[key] = window.setTimeout(() => {
        setFieldState((s) => {
          const next = { ...s };
          delete next[key];
          return next;
        });
      }, 2000);
    }
  }

  async function save(key: string, value: unknown, statusKey?: string): Promise<boolean> {
    const sk = statusKey ?? key;
    mark(sk, { state: "saving" });
    try {
      await api.updateSetting(key, value);
      setSettings((prev) => (prev ? { ...prev, [key]: value } : prev));
      mark(sk, { state: "saved" });
      return true;
    } catch (e) {
      mark(sk, { state: "error", msg: e instanceof Error ? e.message : "failed to save" });
      return false;
    }
  }

  function saveRetryPolicy(
    patch: Record<string, unknown>,
    statusKey: string
  ): Promise<boolean> | boolean {
    if (!settings) return false;
    return save("retry_policy", { ...settings.retry_policy, ...patch }, statusKey);
  }

  async function sendTestNotification() {
    mark("notify_test", { state: "saving" });
    try {
      await api.testNotification();
      mark("notify_test", { state: "saved" });
    } catch (e) {
      mark("notify_test", {
        state: "error",
        msg: e instanceof Error ? e.message : "notification failed",
      });
    }
  }

  if (loadError && !settings) return <p className="text-red-400">{loadError}</p>;
  if (!settings) return <p className="text-ink-500">Loading…</p>;

  function badge(key: string) {
    const f = fieldState[key];
    if (!f) return null;
    const color =
      f.state === "saving"
        ? "text-ink-500"
        : f.state === "saved"
          ? "text-green-300"
          : "text-red-400";
    return (
      <span className={`font-mono text-[11px] ${color}`} title={f.msg}>
        {f.state === "saving" ? "saving" : f.state === "saved" ? "saved" : (f.msg ?? "error")}
      </span>
    );
  }

  const modelInList =
    settings.default_model !== "" && defaultModels.includes(settings.default_model);
  const showCustomModel = defaultModels.length === 0 || !modelInList;

  const noMatches =
    query.trim() !== "" &&
    Object.values(visibleSections).length > 0 &&
    Object.values(visibleSections).every((v) => !v);

  return (
    <div className="space-y-4">
      <header className="animate-fade-up">
        <h1 className="text-3xl font-bold tracking-tight text-ink-100">Settings</h1>
        <p className="mt-1 text-sm text-ink-500">
          Runtime behaviour of the queue and the agent. Most changes apply immediately; artifact
          retention applies on the next start.
        </p>
        <div className="mt-3 flex max-w-md flex-wrap items-center gap-2">
          <div className="relative min-w-52 flex-1">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter settings… (e.g. timeout, model, backup)"
              aria-label="Filter settings"
              className="field w-full pr-8 text-sm"
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery("")}
                aria-label="Clear filter"
                className="absolute right-2 top-1/2 -translate-y-1/2 text-ink-500 hover:text-ink-200"
              >
                ✕
              </button>
            )}
          </div>
          <button
            type="button"
            onClick={() => setAllSections(true)}
            className="text-[11px] text-ink-500 hover:text-ink-300 underline-offset-2 hover:underline"
          >
            Expand all
          </button>
          <span className="text-ink-700" aria-hidden>
            ·
          </span>
          <button
            type="button"
            onClick={() => setAllSections(false)}
            className="text-[11px] text-ink-500 hover:text-ink-300 underline-offset-2 hover:underline"
          >
            Collapse all
          </button>
        </div>
      </header>

      {noMatches && <p className="text-sm text-ink-500">No settings match “{query.trim()}”.</p>}

      {/* Agent defaults */}
      <Section
        key={`agent-${bulkN}`}
        id="agent"
        title="Agent defaults"
        desc="The fallback backend + model used when an operation doesn't pick its own. Every task, follow-up, screen, and agent form can override these per action."
        query={query}
        onVisibility={handleVisibility}
      >
        <div className="grid gap-4 md:grid-cols-2">
          <Row
            label="Default backend"
            desc="The agent backend used when an operation doesn't pick its own."
            status={badge("default_backend")}
            error={fieldState["default_backend"]?.msg}
          >
            <label className="flex items-center gap-2 text-xs text-ink-400">
              Backend
              <select
                value={settings.default_backend}
                onChange={(e) => void save("default_backend", e.target.value)}
                className="field w-44"
                aria-label="Default backend"
              >
                {AGENT_CLIS.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>
          </Row>
          <Row
            label="Default model"
            desc="Applied only on the default backend; other backends use their CLI's own default. Required."
            status={badge("default_model")}
            error={fieldState["default_model"]?.msg}
          >
            <div className="flex flex-col items-end gap-2">
              <label className="flex items-center gap-2 text-xs text-ink-400">
                Model
                <select
                  value={modelInList ? settings.default_model : ""}
                  onChange={(e) => {
                    if (e.target.value) void save("default_model", e.target.value);
                  }}
                  className="field w-44"
                  aria-label="Default model"
                >
                  <option value="" disabled>
                    {defaultModels.length === 0 ? "no models returned" : "select a model"}
                  </option>
                  {defaultModels.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                  {!modelInList && settings.default_model !== "" && (
                    <option value={settings.default_model}>
                      {settings.default_model} (custom)
                    </option>
                  )}
                </select>
              </label>
              {showCustomModel && (
                <label className="flex items-center gap-2 text-xs text-ink-400">
                  {defaultModels.length === 0 ? "Custom model" : "Not listed?"}
                  <TextInput
                    value={modelInList ? "" : settings.default_model}
                    onCommit={(v) => {
                      if (!v.trim()) return false;
                      return save("default_model", v.trim());
                    }}
                    placeholder="type a model name…"
                    ariaLabel="Custom default model"
                    mono
                    className="field w-44 text-xs"
                  />
                </label>
              )}
              {settings.default_model !== "" && !modelInList && defaultModels.length > 0 && (
                <p className="flex flex-wrap items-center gap-2 text-[11px] text-amber-300">
                  <span>
                    ⚠ current value isn&apos;t in this backend&apos;s list — pick one or keep the
                    custom value.
                  </span>
                  <button
                    type="button"
                    onClick={() => void save("default_model", defaultModels[0])}
                    className="underline underline-offset-2 hover:text-amber-200"
                  >
                    Use {defaultModels[0]}
                  </button>
                </p>
              )}
            </div>
          </Row>
          <Row
            label="Model overrides"
            desc="Pin each backend's model dropdown to your own list (e.g. a custom provider) without touching adapter code. Empty = the adapter's own list."
            status={badge("adapter_model_lists")}
            error={fieldState["adapter_model_lists"]?.msg}
          >
            <div className="flex flex-col items-end gap-2">
              {AGENT_CLIS.map((cli) => (
                <label key={cli} className="flex items-center gap-2 text-xs text-ink-400">
                  {cli}
                  <TextInput
                    value={(settings.adapter_model_lists?.[cli] ?? []).join(", ")}
                    onCommit={(v) => {
                      const models = v
                        .split(",")
                        .map((s) => s.trim())
                        .filter(Boolean);
                      return save(
                        "adapter_model_lists",
                        { ...(settings.adapter_model_lists ?? {}), [cli]: models },
                        "adapter_model_lists"
                      );
                    }}
                    placeholder="comma-separated, empty = default"
                    ariaLabel={`Model override for ${cli}`}
                    mono
                    className="field w-64 text-xs"
                  />
                </label>
              ))}
            </div>
          </Row>
        </div>
      </Section>

      {/* Queue & timeouts */}
      <Section
        key={`queue-${bulkN}`}
        id="queue"
        title="Queue & timeouts"
        desc="Parallelism, per-task time budget, stall detection, and artifact retention."
        query={query}
        onVisibility={handleVisibility}
      >
        <div className="grid gap-4 md:grid-cols-2">
          <Row
            label="Queue concurrency"
            desc="Max tasks the agent pool runs in parallel (0 pauses the queue). Applies live."
            status={badge("concurrency")}
            error={fieldState["concurrency"]?.msg}
          >
            <NumberInput
              value={settings.concurrency}
              min={0}
              max={64}
              onCommit={(v) => save("concurrency", v)}
              ariaLabel="Queue concurrency"
            />
          </Row>
          <Row
            label="Auto-publish PRs"
            desc="Open a pull request automatically when a task finishes. Off = hold for manual publish."
            status={badge("auto_publish")}
            error={fieldState["auto_publish"]?.msg}
          >
            <Toggle
              checked={settings.auto_publish}
              onChange={(v) => void save("auto_publish", v)}
              ariaLabel="Auto-publish PRs"
            />
          </Row>
          <Row
            label="Timeout"
            desc="Default minutes a task may run before it is force-killed."
            status={badge("default_timeout_minutes")}
            error={fieldState["default_timeout_minutes"]?.msg}
          >
            <NumberInput
              value={settings.default_timeout_minutes}
              min={1}
              onCommit={(v) => save("default_timeout_minutes", v)}
              ariaLabel="Timeout"
            />
          </Row>
          <Row
            label="Stall timeout"
            desc="Seconds of no agent output before a run is declared hung (e.g. a sub-agent/tool that stops reporting) and auto-recovered."
            status={badge("stall_timeout_seconds")}
            error={fieldState["stall_timeout_seconds"]?.msg}
          >
            <NumberInput
              value={settings.stall_timeout_seconds}
              min={60}
              onCommit={(v) => save("stall_timeout_seconds", v)}
              ariaLabel="Stall timeout"
            />
          </Row>
          <Row
            label="Artifact retention"
            desc="Days to keep task artifacts before cleanup. Applies on the next start."
            status={badge("artifact_ttl_days")}
            error={fieldState["artifact_ttl_days"]?.msg}
          >
            <NumberInput
              value={settings.artifact_ttl_days}
              min={1}
              onCommit={(v) => save("artifact_ttl_days", v)}
              ariaLabel="Artifact retention"
            />
          </Row>
          <Row
            label="Timezone"
            desc="The app's wall clock (screening cron + all timestamps). `local` = this machine's zone; or an IANA name like Asia/Kolkata."
            status={badge("timezone")}
            error={fieldState["timezone"]?.msg}
          >
            <TextInput
              value={settings.timezone ?? "local"}
              onCommit={(v) => save("timezone", v.trim() || "local")}
              placeholder="local"
              ariaLabel="Timezone"
              mono
              className="field w-44 text-xs"
            />
          </Row>
          <Row
            label="Secret patterns"
            desc="Regex patterns (one per line) redacted from agent output."
            status={badge("secret_patterns")}
            error={fieldState["secret_patterns"]?.msg}
          >
            <textarea
              rows={3}
              defaultValue={(settings.secret_patterns ?? []).join("\n")}
              onBlur={(e) =>
                void save(
                  "secret_patterns",
                  e.target.value
                    .split("\n")
                    .map((s) => s.trim())
                    .filter(Boolean)
                )
              }
              placeholder={"AKIA[0-9A-Z]{16}\nsk-[A-Za-z0-9]{20,}"}
              aria-label="Secret patterns"
              className="field max-w-md resize-y font-mono"
            />
          </Row>
        </div>
      </Section>

      {/* Recovery */}
      <Section
        key={`recovery-${bulkN}`}
        id="recovery"
        title="Recovery"
        desc="What happens when a run fails, times out, or stalls. Bounded: a deterministically failing task stops after the attempt cap instead of looping until you cancel it."
        query={query}
        onVisibility={handleVisibility}
      >
        <div className="grid gap-4 md:grid-cols-2">
          <Row
            label="Auto-recovery"
            desc="Re-run failed / timed-out / stalled tasks automatically, up to the attempt cap below. Only the first failure and the final give-up notify."
            status={badge("retry_policy.auto_retry")}
            error={fieldState["retry_policy.auto_retry"]?.msg}
          >
            <Toggle
              checked={settings.retry_policy.auto_retry}
              onChange={(v) => saveRetryPolicy({ auto_retry: v }, "retry_policy.auto_retry")}
              ariaLabel="Auto-recovery"
            />
          </Row>
          <Row
            label="Recovery attempts"
            desc="Max auto-recovery attempts per task. After the cap the task stays failed with a give-up note."
            status={badge("retry_policy.max_attempts")}
            error={fieldState["retry_policy.max_attempts"]?.msg}
          >
            <NumberInput
              value={settings.retry_policy.max_attempts ?? 3}
              min={1}
              max={20}
              onCommit={(v) => saveRetryPolicy({ max_attempts: v }, "retry_policy.max_attempts")}
              ariaLabel="Recovery attempts"
            />
          </Row>
          <Row
            label="Continue prompt"
            desc="Message sent when a timed-out / failed run is resumed (stalls restart fresh)."
            status={badge("retry_policy.continue_prompt")}
            error={fieldState["retry_policy.continue_prompt"]?.msg}
          >
            <TextInput
              value={settings.retry_policy.continue_prompt ?? "continue"}
              onCommit={(v) =>
                saveRetryPolicy(
                  { continue_prompt: v.trim() || "continue" },
                  "retry_policy.continue_prompt"
                )
              }
              ariaLabel="Continue prompt"
              mono
              className="field w-64 text-xs"
            />
          </Row>
          <Row
            label="Timeout multiplier"
            desc="Each recovery multiplies the task's timeout (capped by Max timeout)."
            status={badge("retry_policy.timeout_multiplier")}
            error={fieldState["retry_policy.timeout_multiplier"]?.msg}
          >
            <NumberInput
              value={settings.retry_policy.timeout_multiplier ?? 2}
              min={1}
              onCommit={(v) =>
                saveRetryPolicy({ timeout_multiplier: v }, "retry_policy.timeout_multiplier")
              }
              ariaLabel="Timeout multiplier"
            />
          </Row>
          <Row
            label="Max timeout"
            desc="Ceiling (minutes) a recovered run may reach."
            status={badge("retry_policy.max_timeout_minutes")}
            error={fieldState["retry_policy.max_timeout_minutes"]?.msg}
          >
            <NumberInput
              value={settings.retry_policy.max_timeout_minutes ?? 180}
              min={1}
              onCommit={(v) =>
                saveRetryPolicy({ max_timeout_minutes: v }, "retry_policy.max_timeout_minutes")
              }
              ariaLabel="Max timeout"
            />
          </Row>
          <Row
            label="Non-retryable errors"
            desc="Failures containing one of these phrases (case-insensitive) fail immediately with no recovery — e.g. a wrong model name. One per line."
            status={badge("retry_policy.non_retryable_patterns")}
            error={fieldState["retry_policy.non_retryable_patterns"]?.msg}
          >
            <textarea
              rows={4}
              defaultValue={(settings.retry_policy.non_retryable_patterns ?? []).join("\n")}
              onBlur={(e) =>
                saveRetryPolicy(
                  {
                    non_retryable_patterns: e.target.value
                      .split("\n")
                      .map((s) => s.trim())
                      .filter(Boolean),
                  },
                  "retry_policy.non_retryable_patterns"
                )
              }
              placeholder={"model not found\ninvalid model"}
              aria-label="Non-retryable errors"
              className="field max-w-md resize-y font-mono text-xs"
            />
          </Row>
          <Row
            label="Auto-nudge"
            desc="Automatically queue a follow-up when a task's CI or review state changes after it finished."
            status={badge("auto_nudge")}
            error={fieldState["auto_nudge"]?.msg}
          >
            <Toggle
              checked={settings.auto_nudge ?? false}
              onChange={(v) => void save("auto_nudge", v)}
              ariaLabel="Auto-nudge"
            />
          </Row>
        </div>
      </Section>

      {/* Notifications */}
      <Section
        key={`notifications-${bulkN}`}
        id="notifications"
        title="Notifications"
        desc="Push task lifecycle updates to an ntfy server. The endpoint is either a bare topic name (sent to ntfy.sh) or a full URL to a self-hosted server."
        query={query}
        keywords="ntfy notify push alerts test pings"
        onVisibility={handleVisibility}
      >
        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-4">
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-ink-400">ntfy endpoint</span>
              <TextInput
                value={settings.ntfy_topic}
                onCommit={(v) => save("ntfy_topic", v.trim())}
                placeholder="my-jalebi"
                ariaLabel="ntfy endpoint"
                mono
                className="field max-w-xs text-xs"
              />
              {fieldState["ntfy_topic"]?.msg && (
                <span className="mt-1 block text-[11px] text-red-400">
                  {fieldState["ntfy_topic"].msg}
                </span>
              )}
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-ink-400">
                Progress ping interval (minutes)
              </span>
              <NumberInput
                value={settings.notify_progress_interval_minutes}
                min={1}
                onCommit={(v) => save("notify_progress_interval_minutes", v)}
                ariaLabel="Progress ping interval"
              />
            </label>
            <div className="flex items-center gap-3 pt-1">
              <button
                onClick={sendTestNotification}
                disabled={fieldState["notify_test"]?.state === "saving"}
                className="btn-ghost !px-3 !py-1 text-xs disabled:opacity-40"
              >
                {fieldState["notify_test"]?.state === "saving"
                  ? "Sending…"
                  : "Send test notification"}
              </button>
              {fieldState["notify_test"]?.state === "saved" && (
                <span className="text-xs text-green-300">sent</span>
              )}
              {fieldState["notify_test"]?.state === "error" && (
                <span className="text-xs text-red-400">{fieldState["notify_test"].msg}</span>
              )}
            </div>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="flex items-center justify-between gap-3 rounded border border-ink-800 px-3 py-2.5 text-sm">
              <span>Task done</span>
              <Toggle
                checked={settings.notify_on_done}
                onChange={(v) => void save("notify_on_done", v)}
                ariaLabel="Notify on task done"
              />
            </label>
            <label className="flex items-center justify-between gap-3 rounded border border-ink-800 px-3 py-2.5 text-sm">
              <span>Task failed / timed out / cancelled</span>
              <Toggle
                checked={settings.notify_on_failed}
                onChange={(v) => void save("notify_on_failed", v)}
                ariaLabel="Notify on task failure"
              />
            </label>
            <label className="flex items-center justify-between gap-3 rounded border border-ink-800 px-3 py-2.5 text-sm">
              <span>Still running (interval pings)</span>
              <Toggle
                checked={settings.notify_on_progress}
                onChange={(v) => void save("notify_on_progress", v)}
                ariaLabel="Notify on progress"
              />
            </label>
            <label className="flex items-center justify-between gap-3 rounded border border-ink-800 px-3 py-2.5 text-sm">
              <span>Needs approval</span>
              <Toggle
                checked={settings.notify_on_needs_approval}
                onChange={(v) => void save("notify_on_needs_approval", v)}
                ariaLabel="Notify on needs approval"
              />
            </label>
          </div>
        </div>
      </Section>

      {/* Webhooks */}
      <Section
        key={`webhooks-${bulkN}`}
        id="webhooks"
        title="Webhooks"
        desc="Event-driven triggers are delivered by GitHub to the local listener. For a localhost install, expose Jalebi via a tunnel (cloudflared/ngrok) and set the public URL here; the optional secret signs deliveries (X-Hub-Signature-256)."
        query={query}
        keywords="webhook tunnel github delivery secret url"
        onVisibility={handleVisibility}
      >
        <div className="grid gap-4 md:grid-cols-2">
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-ink-400">
              Public webhook URL (tunnel base)
            </span>
            <TextInput
              value={settings.webhook_url}
              onCommit={(v) => save("webhook_url", v.trim())}
              placeholder="https://jalebi.example.tunnel"
              ariaLabel="Public webhook URL"
              mono
              className="field text-xs"
            />
            <span className="mt-1 block text-[11px] text-ink-500">
              GitHub posts to{" "}
              <span className="font-mono">
                {settings.webhook_url
                  ? `${settings.webhook_url.replace(/\/$/, "")}/webhook`
                  : "<url>/webhook"}
              </span>
            </span>
          </label>
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-ink-400">
              Webhook secret (optional)
            </span>
            <input
              type="password"
              defaultValue={settings.webhook_secret}
              onBlur={(e) => void save("webhook_secret", e.target.value)}
              placeholder="••••••••"
              aria-label="Webhook secret"
              className="field font-mono text-xs"
            />
            <span className="mt-1 block text-[11px] text-ink-500">
              When set, deliveries are verified against this HMAC secret.
            </span>
          </label>
        </div>
      </Section>

      <IDESettings query={query} onVisibility={handleVisibility} resetKey={bulkN} />

      <EnvVarsSection
        repos={repos}
        reposLoaded={reposLoaded}
        query={query}
        onVisibility={handleVisibility}
        resetKey={bulkN}
      />

      <DataSection query={query} onVisibility={handleVisibility} resetKey={bulkN} />
    </div>
  );
}
