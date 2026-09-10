import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { EmptyState } from "../components/EmptyState";
import SearchableSelect from "../components/SearchableSelect";
import { useBackends } from "../hooks/useBackends";
import { AVATARS, avatarFor, avatarUrl, suggestAvatar } from "../lib/agentAvatars";
import type { AgentUsage, CatalogAgent, LibrarySkill } from "../types";

const EMPTY: CatalogAgent = {
  id: "",
  name: "",
  kind: "general",
  cli: null,
  model: null,
  personality_md: "",
  skills: [],
  skill_ids: [],
  custom_instructions: "",
  enabled: true,
  description: "",
  avatar: null,
  created_at: "",
};

function AgentForm({
  agent,
  onSaved,
  onCancel,
}: {
  agent: CatalogAgent | null;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const isEdit = agent !== null;
  // Normalize: older payloads (and tests) may omit the newer fields.
  const [form, setForm] = useState<CatalogAgent>(() => ({ ...EMPTY, ...(agent ?? {}) }));
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [library, setLibrary] = useState<LibrarySkill[] | null>(null);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const [skillQuery, setSkillQuery] = useState("");
  const backendOptions = useBackends();

  // Live avatar suggestion from name+description (the "Auto" choice).
  const suggested = suggestAvatar(form.name, form.description ?? "");
  const effectiveAvatar = form.avatar ?? suggested;

  useEffect(() => {
    // Follow the CLI override selected in THIS form (blank = global default).
    // The cancelled guard drops a stale response if the override changes again
    // mid-fetch.
    let cancelled = false;
    api
      .getModels(form.cli ?? undefined)
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
  }, [form.cli]);

  useEffect(() => {
    // Library skills for the attach picker. A failure is shown with a retry
    // (there is no inline fallback anymore — skills live in the library).
    let cancelled = false;
    async function fetchLibrary() {
      try {
        const list = await api.getSkills();
        if (!cancelled) {
          setLibrary(list);
          setLibraryError(null);
        }
      } catch (e) {
        if (!cancelled) {
          setLibrary(null);
          setLibraryError(e instanceof Error ? e.message : "failed to load skills");
        }
      }
    }
    fetchLibrary();
    return () => {
      cancelled = true;
    };
  }, []);

  function set(patch: Partial<CatalogAgent>) {
    setForm((f) => ({ ...f, ...patch }));
  }

  const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!form.id.trim() || !form.name.trim()) {
      setMsg({ kind: "err", text: "id and name are required" });
      return;
    }
    if (!isEdit && !SLUG_RE.test(form.id.trim())) {
      setMsg({
        kind: "err",
        text: "id must be lowercase letters/digits with single hyphens (e.g. security-auditor)",
      });
      return;
    }
    setBusy(true);
    setMsg(null);
    // Skills come from the library only (skill_ids). Inline extras are legacy:
    // saving clears them — manage skills in the Skills section instead.
    const payload = {
      name: form.name,
      kind: form.kind,
      cli: form.cli,
      model: form.model,
      personality_md: form.personality_md,
      skills: [],
      skill_ids: form.skill_ids ?? [],
      custom_instructions: form.custom_instructions,
      enabled: form.enabled,
      description: form.description ?? "",
      avatar: form.avatar,
    };
    try {
      if (isEdit) {
        await api.updateAgent(agent.id, payload);
      } else {
        await api.createAgent({ id: form.id.trim(), ...payload });
      }
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
        <h2 className="panel-title">{isEdit ? `Edit agent — ${agent.id}` : "New catalog agent"}</h2>
        <span className="eyebrow">personality → AGENTS.md · skills → @path</span>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-ink-400">Slug (id)</span>
          <input
            value={form.id}
            disabled={isEdit}
            onChange={(e) => set({ id: e.target.value })}
            placeholder="security-auditor"
            className="field font-mono"
          />
          {!isEdit && (
            <span className="mt-1 block text-[11px] text-ink-600">
              Lowercase letters/digits with single hyphens. Permanent — tasks and rules reference
              it.
            </span>
          )}
        </label>
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-ink-400">Name</span>
          <input
            value={form.name}
            onChange={(e) => set({ name: e.target.value })}
            placeholder="Security Auditor"
            className="field"
          />
        </label>
        <div>
          <SearchableSelect
            label="Kind"
            value={form.kind}
            onChange={(v) => set({ kind: v as "general" | "reviewer" })}
            options={["general", "reviewer"]}
          />
          <span className="mt-1 block text-[11px] text-ink-600">
            {form.kind === "reviewer"
              ? "Reviewer: runs the PR review workflow and is selectable in trigger rules."
              : "General: plain build agent for issue_fix / freeform tasks."}
          </span>
        </div>
      </div>

      <label className="block">
        <span className="mb-1.5 block text-xs font-medium text-ink-400">
          Description (one line, shown on cards and in pickers)
        </span>
        <input
          value={form.description ?? ""}
          onChange={(e) => set({ description: e.target.value })}
          placeholder="Finds vulns and bad security practices."
          className="field"
        />
      </label>

      <div>
        <span className="mb-1.5 block text-xs font-medium text-ink-400">
          Profile picture{" "}
          <span className="text-ink-600">
            auto-suggested from name/description: {suggested} (override anytime)
          </span>
        </span>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => set({ avatar: null })}
            title="Auto-assign from name and description"
            className={`flex items-center gap-2 rounded-lg border px-2 py-1.5 text-xs transition-colors ${
              form.avatar === null
                ? "border-syrup-500 text-syrup-300"
                : "border-ink-800 text-ink-400 hover:text-ink-100"
            }`}
          >
            <img src={avatarUrl(suggested)} alt="" className="h-8 w-8" />
            Auto
          </button>
          {AVATARS.map((a) => (
            <button
              key={a.id}
              type="button"
              onClick={() => set({ avatar: a.id })}
              title={a.label}
              className={`rounded-lg border p-1.5 transition-colors ${
                effectiveAvatar === a.id && form.avatar !== null
                  ? "border-syrup-500"
                  : "border-ink-800 hover:border-ink-600"
              }`}
            >
              <img src={avatarUrl(a.id)} alt={a.label} className="h-8 w-8" />
            </button>
          ))}
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <SearchableSelect
          label="CLI override (optional)"
          value={form.cli ?? ""}
          onChange={(v) => {
            set({ cli: v });
            // The old model pin belonged to the old backend — drop it rather
            // than running an invalid combination (the server does the same).
            set({ model: "" });
          }}
          placeholder="default (global setting)"
          options={backendOptions}
        />
        <div>
          <SearchableSelect
            label="Model pin (optional)"
            value={form.model ?? ""}
            onChange={(v) => set({ model: v || "" })}
            placeholder="no pin (CLI default)"
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

      <label className="block">
        <span className="mb-1.5 block text-xs font-medium text-ink-400">
          Personality (markdown, merged into the worktree AGENTS.md)
        </span>
        <textarea
          value={form.personality_md}
          onChange={(e) => set({ personality_md: e.target.value })}
          rows={6}
          placeholder="You are a senior application security engineer. Be adversarial…"
          className="field resize-y font-mono text-sm"
        />
      </label>

      <div>
        <span className="mb-1.5 block text-xs font-medium text-ink-400">
          Skills (from the library only
          {library !== null && (
            <span className="text-ink-600">
              {" "}
              · {(form.skill_ids ?? []).length} of {library.length} selected
            </span>
          )}
          )
        </span>
        <input
          value={skillQuery}
          onChange={(e) => setSkillQuery(e.target.value)}
          placeholder="Search skills…"
          className="field mb-2 !py-1.5 text-sm"
        />
        {libraryError && (
          <p className="mb-2 text-xs text-red-400">
            Couldn&apos;t load the skill library ({libraryError}).{" "}
            <button
              type="button"
              onClick={() => {
                setLibraryError(null);
                api
                  .getSkills()
                  .then(setLibrary)
                  .catch((e) =>
                    setLibraryError(e instanceof Error ? e.message : "failed to load skills")
                  );
              }}
              className="link text-xs"
            >
              Retry
            </button>
          </p>
        )}
        {library !== null && (
          <div className="max-h-44 space-y-1 overflow-y-auto rounded-lg border border-ink-800 p-2">
            {library
              .filter((s) => {
                const q = skillQuery.trim().toLowerCase();
                if (!q) return true;
                return `${s.id} ${s.name} ${s.description}`.toLowerCase().includes(q);
              })
              .map((s) => {
                const linked = (form.skill_ids ?? []).includes(s.id);
                return (
                  <label
                    key={s.id}
                    className="flex cursor-pointer items-start gap-2 rounded px-2 py-1.5 text-sm hover:bg-ink-850"
                  >
                    <input
                      type="checkbox"
                      checked={linked}
                      onChange={() =>
                        set({
                          skill_ids: linked
                            ? (form.skill_ids ?? []).filter((id) => id !== s.id)
                            : [...(form.skill_ids ?? []), s.id],
                        })
                      }
                      className="mt-1 h-4 w-4 rounded border-ink-700 bg-ink-900"
                    />
                    <span className="min-w-0">
                      <span className="font-mono text-xs text-syrup-300">{s.id}</span>
                      <span className="ml-2 text-ink-200">{s.name}</span>
                      {s.description && (
                        <span className="block truncate text-[11px] text-ink-500">
                          {s.description}
                        </span>
                      )}
                    </span>
                  </label>
                );
              })}
          </div>
        )}
        <span className="mt-1 block text-[11px] text-ink-600">
          To add or edit a skill itself, use the Skills section.
        </span>
      </div>

      <label className="block">
        <span className="mb-1.5 block text-xs font-medium text-ink-400">
          Custom instructions (appended to the task prompt){" "}
          <span className="text-ink-600">
            {form.custom_instructions.length.toLocaleString()}/20,000
          </span>
        </span>
        <textarea
          value={form.custom_instructions}
          onChange={(e) => set({ custom_instructions: e.target.value })}
          rows={3}
          placeholder="Pay extra attention to auth, secrets, and injection."
          className="field resize-y"
        />
      </label>

      <label className="flex items-center gap-2 text-sm text-ink-300">
        <input
          type="checkbox"
          checked={form.enabled}
          onChange={(e) => set({ enabled: e.target.checked })}
          className="h-4 w-4 rounded border-ink-700 bg-ink-900"
        />
        Enabled (selectable in new tasks)
      </label>

      {msg && (
        <p className={`text-xs ${msg.kind === "ok" ? "text-green-400" : "text-red-400"}`}>
          {msg.text}
        </p>
      )}

      <div className="flex justify-end gap-2">
        <button type="button" onClick={onCancel} className="btn-ghost">
          Cancel
        </button>
        <button type="submit" disabled={busy} className="btn-primary">
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </form>
  );
}

function AgentRow({
  agent,
  usage,
  onToggle,
  onEdit,
  onDelete,
}: {
  agent: CatalogAgent;
  usage: AgentUsage | null;
  onToggle: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="surface flex flex-wrap items-start justify-between gap-4 p-5">
      <img
        src={avatarUrl(avatarFor(agent))}
        alt=""
        title={`avatar: ${avatarFor(agent)}${agent.avatar ? "" : " (auto)"}`}
        className="h-11 w-11 shrink-0"
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm font-semibold text-syrup-300">{agent.id}</span>
          <span className="rounded-full border border-ink-800 px-2 py-0.5 text-[11px] text-ink-400">
            {agent.kind}
          </span>
          {!agent.enabled && (
            <span className="rounded-full border border-red-900 px-2 py-0.5 text-[11px] text-red-400">
              disabled
            </span>
          )}
        </div>
        <p className="mt-1 text-sm font-medium text-ink-100">{agent.name}</p>
        {agent.description && (
          <p className="mt-0.5 line-clamp-2 text-xs text-ink-400">{agent.description}</p>
        )}
        <div className="mt-1.5 flex flex-wrap gap-1.5 text-[11px] text-ink-500">
          {agent.cli && <span className="font-mono">cli: {agent.cli}</span>}
          {agent.model && <span className="font-mono">model: {agent.model}</span>}
          {agent.skills.length > 0 && (
            <span className="font-mono">{agent.skills.length} skill(s)</span>
          )}
        </div>
        {usage ? (
          <p className="mt-1.5 text-[11px] text-ink-500">
            Used by {usage.task_count} task{usage.task_count === 1 ? "" : "s"}
            {usage.trigger_rules.length > 0 &&
              ` · ${usage.trigger_rules.length} trigger rule${usage.trigger_rules.length === 1 ? "" : "s"}: ${usage.trigger_rules
                .map((r) => `#${r.id} ${r.event}`)
                .join(", ")}`}
          </p>
        ) : (
          <p className="mt-1.5 text-[11px] text-ink-600">Usage unavailable.</p>
        )}
        {agent.personality_md && (
          <p className="mt-2 line-clamp-2 text-xs text-ink-400">{agent.personality_md}</p>
        )}
      </div>
      <div className="flex shrink-0 gap-2">
        <button onClick={onToggle} className="btn-ghost text-xs">
          {agent.enabled ? "Disable" : "Enable"}
        </button>
        <button onClick={onEdit} className="btn-ghost text-xs">
          Edit
        </button>
        <button onClick={onDelete} className="btn-ghost !text-red-400 text-xs">
          Delete
        </button>
      </div>
    </div>
  );
}

export default function Agents() {
  const [agents, setAgents] = useState<CatalogAgent[]>([]);
  const [usageById, setUsageById] = useState<Record<string, AgentUsage | null>>({});
  const [editing, setEditing] = useState<CatalogAgent | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [kindFilter, setKindFilter] = useState<"all" | "general" | "reviewer">("all");
  const [statusFilter, setStatusFilter] = useState<"all" | "enabled" | "disabled">("all");
  const [sort, setSort] = useState<"name" | "kind" | "newest">("name");

  function load() {
    api
      .getAgents()
      .then((list) => {
        setAgents(list);
        // Usage per agent, best-effort: a failure leaves "unavailable", never
        // blocks the list.
        list.forEach((a) => {
          api
            .getAgentUsage(a.id)
            .then((u) => setUsageById((prev) => ({ ...prev, [a.id]: u })))
            .catch(() => setUsageById((prev) => ({ ...prev, [a.id]: null })));
        });
      })
      .catch((e) => setError(e.message));
  }

  useEffect(load, []);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = agents.filter((a) => {
      if (kindFilter !== "all" && a.kind !== kindFilter) return false;
      if (statusFilter === "enabled" && !a.enabled) return false;
      if (statusFilter === "disabled" && a.enabled) return false;
      if (!q) return true;
      return `${a.id} ${a.name} ${a.description ?? ""}`.toLowerCase().includes(q);
    });
    return [...filtered].sort((x, y) => {
      if (sort === "kind") return x.kind.localeCompare(y.kind) || x.name.localeCompare(y.name);
      if (sort === "newest") return y.created_at.localeCompare(x.created_at);
      return x.name.localeCompare(y.name);
    });
  }, [agents, query, kindFilter, statusFilter, sort]);

  async function toggle(agent: CatalogAgent) {
    try {
      await api.updateAgent(agent.id, { enabled: !agent.enabled });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to update");
    }
  }

  async function remove(agent: CatalogAgent) {
    const usage = usageById[agent.id];
    const rules = usage?.trigger_rules ?? [];
    const impact =
      rules.length > 0
        ? `\n\nWARNING: ${rules.length} trigger rule${rules.length === 1 ? "" : "s"} reference${
            rules.length === 1 ? "s" : ""
          } this agent (${rules.map((r) => `#${r.id} ${r.event}`).join(", ")}) — deleting will break ${
            rules.length === 1 ? "it" : "them"
          } at dispatch.`
        : "";
    if (!window.confirm(`Delete catalog agent "${agent.id}"? Tasks keep their history.${impact}`))
      return;
    try {
      await api.deleteAgent(agent.id);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to delete");
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between animate-fade-up">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-ink-100">Agents</h1>
          <p className="mt-1 text-sm text-ink-500">
            Named agents = personality (→ AGENTS.md) + skills + optional CLI/model. Reviewers of
            kind <code className="font-mono text-ink-400">reviewer</code> get the review workflow.
          </p>
        </div>
        <button
          onClick={() => {
            setEditing(null);
            setShowForm(true);
          }}
          className="btn-primary"
        >
          + New agent
        </button>
      </header>

      {error && <p className="text-sm text-red-400">{error}</p>}

      <div className="flex flex-wrap items-center gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search id, name, description…"
          className="field max-w-xs !py-1.5 text-sm"
        />
        <SearchableSelect
          label="Sort agents"
          value={sort}
          onChange={(v) => setSort(v as "name" | "kind" | "newest")}
          options={[
            { value: "name", label: "Sort: name" },
            { value: "kind", label: "Sort: kind" },
            { value: "newest", label: "Sort: newest" },
          ]}
        />
        {(["all", "general", "reviewer"] as const).map((k) => (
          <button
            key={k}
            onClick={() => setKindFilter(k)}
            className={`rounded-full border px-2.5 py-1 text-[11px] transition-colors ${
              kindFilter === k
                ? "border-syrup-500 text-syrup-300"
                : "border-ink-800 text-ink-400 hover:text-ink-100"
            }`}
          >
            {k === "all" ? "All kinds" : k}
          </button>
        ))}
        {(["all", "enabled", "disabled"] as const).map((s) => (
          <button
            key={s}
            onClick={() => setStatusFilter(s)}
            className={`rounded-full border px-2.5 py-1 text-[11px] transition-colors ${
              statusFilter === s
                ? "border-syrup-500 text-syrup-300"
                : "border-ink-800 text-ink-400 hover:text-ink-100"
            }`}
          >
            {s === "all" ? "Any status" : s}
          </button>
        ))}
      </div>

      {showForm && (
        <AgentForm
          key={editing?.id ?? "new"}
          agent={editing}
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
      )}

      {visible.length === 0 && !showForm ? (
        agents.length === 0 ? (
          <EmptyState
            icon="🤖"
            title="No catalog agents yet"
            description="Create an agent to give tasks a personality, skills, and optional model/CLI pins."
            action={
              <button
                type="button"
                onClick={() => {
                  setEditing(null);
                  setShowForm(true);
                }}
                className="btn-primary"
              >
                Create agent
              </button>
            }
          />
        ) : (
          <div className="surface flex flex-col items-start gap-3 p-6 animate-fade-up">
            <h2 className="panel-title">No agents match</h2>
            <p className="text-sm text-ink-400">Try a different search or clear the filters.</p>
          </div>
        )
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {visible.map((a) => (
            <AgentRow
              key={a.id}
              agent={a}
              usage={usageById[a.id] ?? null}
              onToggle={() => toggle(a)}
              onEdit={() => {
                setEditing(a);
                setShowForm(true);
              }}
              onDelete={() => remove(a)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
