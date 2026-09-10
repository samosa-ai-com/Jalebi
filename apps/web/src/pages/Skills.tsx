import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { EmptyState } from "../components/EmptyState";
import SearchableSelect from "../components/SearchableSelect";
import type { LibrarySkill, SkillUsage } from "../types";

const EMPTY: LibrarySkill = {
  id: "",
  name: "",
  description: "",
  content: "",
  tags: [],
  created_at: "",
  updated_at: "",
};

const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

function SkillForm({
  skill,
  onSaved,
  onCancel,
}: {
  skill: LibrarySkill | null;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const isEdit = skill !== null;
  const [form, setForm] = useState<LibrarySkill>(skill ?? { ...EMPTY });
  const [tagsText, setTagsText] = useState((skill?.tags ?? []).join(", "));
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  function set(patch: Partial<LibrarySkill>) {
    setForm((f) => ({ ...f, ...patch }));
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!form.id.trim() || !form.name.trim()) {
      setMsg({ kind: "err", text: "id and name are required" });
      return;
    }
    if (!isEdit && !SLUG_RE.test(form.id.trim())) {
      setMsg({
        kind: "err",
        text: "id must be lowercase letters/digits with single hyphens (e.g. secure-coding)",
      });
      return;
    }
    const tags = tagsText
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
    setBusy(true);
    setMsg(null);
    try {
      const payload = {
        name: form.name,
        description: form.description,
        content: form.content,
        tags,
      };
      if (isEdit) {
        await api.updateSkill(skill.id, payload);
      } else {
        await api.createSkill({ id: form.id.trim(), ...payload });
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
        <h2 className="panel-title">{isEdit ? `Edit skill — ${skill.id}` : "New skill"}</h2>
        <span className="eyebrow">markdown → worktree .claude/skills</span>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-ink-400">Slug (id)</span>
          <input
            value={form.id}
            disabled={isEdit}
            onChange={(e) => set({ id: e.target.value })}
            placeholder="secure-coding"
            className="field font-mono"
          />
          {!isEdit && (
            <span className="mt-1 block text-[11px] text-ink-600">
              Lowercase letters/digits with single hyphens. Permanent — agents link it.
            </span>
          )}
        </label>
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-ink-400">Name</span>
          <input
            value={form.name}
            onChange={(e) => set({ name: e.target.value })}
            placeholder="Secure Coding"
            className="field"
          />
        </label>
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-ink-400">
            Tags (comma-separated)
          </span>
          <input
            value={tagsText}
            onChange={(e) => setTagsText(e.target.value)}
            placeholder="security, review"
            className="field font-mono text-sm"
          />
        </label>
      </div>

      <label className="block">
        <span className="mb-1.5 block text-xs font-medium text-ink-400">
          Description (one line, shown in pickers and cards)
        </span>
        <input
          value={form.description}
          onChange={(e) => set({ description: e.target.value })}
          placeholder="Security checklist for any codebase."
          className="field"
        />
      </label>

      <label className="block">
        <span className="mb-1.5 block text-xs font-medium text-ink-400">
          Content (markdown, loaded by agents via @path){" "}
          <span className="text-ink-600">{form.content.length.toLocaleString()}/100,000</span>
        </span>
        <textarea
          value={form.content}
          onChange={(e) => set({ content: e.target.value })}
          rows={10}
          placeholder="# Secure coding&#10;&#10;Never use eval…"
          className="field resize-y font-mono text-sm"
        />
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

function SkillCard({
  skill,
  usage,
  onEdit,
  onDelete,
}: {
  skill: LibrarySkill;
  usage: SkillUsage | null;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const agents = usage?.agents ?? [];
  return (
    <div className="surface flex flex-wrap items-start justify-between gap-4 p-5">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm font-semibold text-syrup-300">{skill.id}</span>
          {skill.tags.map((t) => (
            <span
              key={t}
              className="rounded-full border border-ink-800 px-2 py-0.5 text-[11px] text-ink-400"
            >
              {t}
            </span>
          ))}
        </div>
        <p className="mt-1 text-sm font-medium text-ink-100">{skill.name}</p>
        {skill.description && (
          <p className="mt-1 line-clamp-2 text-xs text-ink-400">{skill.description}</p>
        )}
        {usage ? (
          <p className="mt-1.5 text-[11px] text-ink-500">
            {agents.length === 0
              ? "Not linked by any agent."
              : `Linked by ${agents.length} agent${agents.length === 1 ? "" : "s"}: ${agents
                  .map((a) => a.id)
                  .join(", ")}`}
          </p>
        ) : (
          <p className="mt-1.5 text-[11px] text-ink-600">Usage unavailable.</p>
        )}
      </div>
      <div className="flex shrink-0 gap-2">
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

type SortKey = "name" | "updated" | "used";

export default function Skills() {
  const [skills, setSkills] = useState<LibrarySkill[]>([]);
  const [usageById, setUsageById] = useState<Record<string, SkillUsage | null>>({});
  const [editing, setEditing] = useState<LibrarySkill | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [tagFilter, setTagFilter] = useState<string | null>(null);
  const [sort, setSort] = useState<SortKey>("name");

  function load() {
    api
      .getSkills()
      .then((list) => {
        setSkills(list);
        // Usage per skill, best-effort: a failure leaves "unavailable", never
        // blocks the list.
        list.forEach((s) => {
          api
            .getSkillUsage(s.id)
            .then((u) => setUsageById((prev) => ({ ...prev, [s.id]: u })))
            .catch(() => setUsageById((prev) => ({ ...prev, [s.id]: null })));
        });
      })
      .catch((e) => setError(e.message));
  }

  useEffect(load, []);

  const allTags = useMemo(() => {
    const tags = new Set<string>();
    skills.forEach((s) => s.tags.forEach((t) => tags.add(t)));
    return [...tags].sort();
  }, [skills]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = skills.filter((s) => {
      if (tagFilter && !s.tags.includes(tagFilter)) return false;
      if (!q) return true;
      return `${s.id} ${s.name} ${s.description} ${s.content}`.toLowerCase().includes(q);
    });
    const used = (id: string) => usageById[id]?.agents.length ?? -1;
    return [...filtered].sort((a, b) => {
      if (sort === "updated") return b.updated_at.localeCompare(a.updated_at);
      if (sort === "used") return used(b.id) - used(a.id);
      return a.name.localeCompare(b.name);
    });
  }, [skills, query, tagFilter, sort, usageById]);

  async function remove(skill: LibrarySkill) {
    const agents = usageById[skill.id]?.agents ?? [];
    const impact =
      agents.length > 0
        ? `\n\nWARNING: ${agents.length} agent${agents.length === 1 ? "" : "s"} link${
            agents.length === 1 ? "s" : ""
          } this skill (${agents
            .map((a) => a.id)
            .join(", ")}) — the server will refuse the delete until they unlink it.`
        : "";
    if (!window.confirm(`Delete skill "${skill.id}"?${impact}`)) return;
    try {
      await api.deleteSkill(skill.id);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to delete");
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between animate-fade-up">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-ink-100">Skills</h1>
          <p className="mt-1 text-sm text-ink-500">
            Reusable markdown knowledge agents link by reference — one edit updates every linked
            agent. {skills.length} skill{skills.length === 1 ? "" : "s"}.
          </p>
        </div>
        <button
          onClick={() => {
            setEditing(null);
            setShowForm(true);
          }}
          className="btn-primary"
        >
          + New skill
        </button>
      </header>

      {error && <p className="text-sm text-red-400">{error}</p>}

      {showForm && (
        <SkillForm
          key={editing?.id ?? "new"}
          skill={editing}
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

      <div className="flex flex-wrap items-center gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search id, name, description, content…"
          className="field max-w-xs !py-1.5 text-sm"
        />
        <SearchableSelect
          label="Sort skills"
          hideLabel
          value={sort}
          onChange={(v) => setSort(v as SortKey)}
          options={[
            { value: "name", label: "Sort: name" },
            { value: "updated", label: "Sort: recently updated" },
            { value: "used", label: "Sort: most used" },
          ]}
        />
        {allTags.map((t) => (
          <button
            key={t}
            onClick={() => setTagFilter((f) => (f === t ? null : t))}
            className={`rounded-full border px-2.5 py-1 text-[11px] transition-colors ${
              tagFilter === t
                ? "border-syrup-500 text-syrup-300"
                : "border-ink-800 text-ink-400 hover:text-ink-100"
            }`}
          >
            {t}
          </button>
        ))}
        {tagFilter && (
          <button onClick={() => setTagFilter(null)} className="btn-ghost !py-1 text-[11px]">
            Clear tag
          </button>
        )}
      </div>

      {visible.length === 0 && !showForm ? (
        skills.length === 0 ? (
          <EmptyState
            icon="🧩"
            title="No skills yet"
            description="Create a skill to provide reusable instructions and context across your agents."
            action={
              <button
                type="button"
                onClick={() => {
                  setEditing(null);
                  setShowForm(true);
                }}
                className="btn-primary"
              >
                Create skill
              </button>
            }
          />
        ) : (
          <div className="surface flex flex-col items-start gap-3 p-6 animate-fade-up">
            <h2 className="panel-title">No skills match</h2>
            <p className="text-sm text-ink-400">Try a different search or clear the tag filter.</p>
          </div>
        )
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {visible.map((s) => (
            <SkillCard
              key={s.id}
              skill={s}
              usage={usageById[s.id] ?? null}
              onEdit={() => {
                setEditing(s);
                setShowForm(true);
              }}
              onDelete={() => remove(s)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
