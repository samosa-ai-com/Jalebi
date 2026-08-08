import { useEffect, useState } from "react";
import { api } from "../api/client";
import type {
  CatalogAgent,
  EventDelivery,
  Repo,
  TriggerRule,
  WebhookStatus,
} from "../types";

const EVENTS = [
  "pull_request.opened",
  "pull_request.synchronize",
  "pull_request.reopened",
  "pull_request_review",
  "issues.opened",
  "push",
];

const ACTIONS = ["start_review", "triage_issue", "create_task", "rerun_review"];

function RuleForm({
  repos,
  agents,
  editing,
  onSaved,
  onCancel,
}: {
  repos: Repo[];
  agents: CatalogAgent[];
  editing: TriggerRule | null;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [repoId, setRepoId] = useState(editing?.repo_id ?? repos[0]?.id ?? 0);
  const [event, setEvent] = useState(editing?.event ?? "pull_request.opened");
  const [action, setAction] = useState(editing?.action ?? "start_review");
  const [branchFilter, setBranchFilter] = useState(editing?.branch_filter ?? "");
  const [authorFilter, setAuthorFilter] = useState(editing?.author_filter ?? "");
  const [labels, setLabels] = useState((editing?.label_filter ?? []).join(", "));
  const [agentIds, setAgentIds] = useState<string[]>(editing?.agent_ids ?? []);
  const [instructions, setInstructions] = useState(editing?.custom_instructions ?? "");
  const [enabled, setEnabled] = useState(editing?.enabled ?? true);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const reviewers = agents.filter((a) => a.kind === "reviewer");

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    const body = {
      event,
      action,
      branch_filter: branchFilter || undefined,
      author_filter: authorFilter || undefined,
      label_filter: labels
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
      agent_ids: agentIds,
      custom_instructions: instructions || undefined,
      enabled,
    };
    try {
      if (editing) {
        await api.updateTriggerRule(editing.id, body);
      } else {
        await api.createTriggerRule({ repo_id: repoId, ...body });
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
        <h2 className="panel-title">{editing ? `Edit rule #${editing.id}` : "New trigger rule"}</h2>
        <span className="eyebrow">event → action → agents</span>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-ink-400">Repo</span>
          <select value={repoId} onChange={(e) => setRepoId(Number(e.target.value))} className="field" disabled={!!editing}>
            {repos.map((r) => (
              <option key={r.id} value={r.id}>
                {r.full_name}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-ink-400">Event</span>
          <select value={event} onChange={(e) => setEvent(e.target.value)} className="field font-mono">
            {EVENTS.map((e) => (
              <option key={e} value={e}>
                {e}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-ink-400">Action</span>
          <select value={action} onChange={(e) => setAction(e.target.value)} className="field font-mono">
            {ACTIONS.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-ink-400">Branch filter (optional)</span>
          <input value={branchFilter} onChange={(e) => setBranchFilter(e.target.value)} placeholder="main" className="field font-mono" />
        </label>
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-ink-400">Author filter (optional)</span>
          <input value={authorFilter} onChange={(e) => setAuthorFilter(e.target.value)} placeholder="octocat" className="field font-mono" />
        </label>
      </div>

      <label className="block">
        <span className="mb-1.5 block text-xs font-medium text-ink-400">Label filter (comma-separated, optional)</span>
        <input value={labels} onChange={(e) => setLabels(e.target.value)} placeholder="bug, frontend" className="field" />
      </label>

      {action === "start_review" && (
        <fieldset>
          <legend className="mb-1.5 block text-xs font-medium text-ink-400">
            Reviewers (catalog agents, kind reviewer)
          </legend>
          <div className="flex flex-wrap gap-2">
            {reviewers.map((a) => {
              const checked = agentIds.includes(a.id);
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
                      setAgentIds((prev) => (checked ? prev.filter((n) => n !== a.id) : [...prev, a.id]))
                    }
                    className="hidden"
                  />
                  {a.name} ({a.id})
                </label>
              );
            })}
            {reviewers.length === 0 && (
              <span className="text-xs text-ink-500">
                No reviewer agents yet — create one on the Agents page.
              </span>
            )}
          </div>
        </fieldset>
      )}

      {action !== "start_review" && (
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-ink-400">Custom instructions (task prompt)</span>
          <textarea
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            rows={2}
            className="field resize-y"
          />
        </label>
      )}

      <label className="flex items-center gap-2 text-sm text-ink-300">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} className="h-4 w-4 rounded border-ink-700 bg-ink-900" />
        Enabled
      </label>

      {msg && <p className={`text-xs ${msg.kind === "ok" ? "text-green-400" : "text-red-400"}`}>{msg.text}</p>}

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

function DeliveryRow({ d }: { d: EventDelivery }) {
  const [replayed, setReplayed] = useState<string | null>(null);
  async function replay() {
    try {
      await api.replayDelivery(d.id);
      setReplayed("replayed ✓");
    } catch (err) {
      setReplayed(err instanceof Error ? err.message : "replay failed");
    }
  }
  return (
    <li className="flex flex-wrap items-center gap-2 py-2 text-xs">
      <span className={`rounded-full border px-2 py-0.5 font-mono ${
        d.status === "matched" ? "border-green-900 text-green-400"
        : d.status === "failed" ? "border-red-900 text-red-400"
        : "border-ink-800 text-ink-500"
      }`}>
        {d.status}
      </span>
      <span className="font-mono text-ink-300">{d.event}</span>
      {d.action && <span className="text-ink-500">· {d.action}</span>}
      <span className="text-ink-500">{d.repo_full_name ?? "—"}</span>
      <span className="ml-auto font-mono text-ink-600">{d.received_at.slice(11, 19)}</span>
      <button onClick={replay} className="btn-ghost !px-2 !py-0.5">
        replay
      </button>
      {replayed && <span className="text-ink-500">{replayed}</span>}
    </li>
  );
}

export default function Triggers() {
  const [repos, setRepos] = useState<Repo[]>([]);
  const [agents, setAgents] = useState<CatalogAgent[]>([]);
  const [rules, setRules] = useState<TriggerRule[]>([]);
  const [deliveries, setDeliveries] = useState<EventDelivery[]>([]);
  const [status, setStatus] = useState<WebhookStatus | null>(null);
  const [editing, setEditing] = useState<TriggerRule | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function load() {
    api
      .getTriggerRules()
      .then(setRules)
      .catch((e) => setError(e.message));
    api
      .getDeliveries()
      .then(setDeliveries)
      .catch(() => {});
    api
      .getWebhookStatus()
      .then(setStatus)
      .catch(() => {});
    api
      .getRepos()
      .then(setRepos)
      .catch(() => {});
    api
      .getAgents(true)
      .then(setAgents)
      .catch(() => {});
  }

  useEffect(load, []);

  const repoName = (id: number) => repos.find((r) => r.id === id)?.full_name ?? `repo#${id}`;

  async function toggleRule(rule: TriggerRule) {
    try {
      await api.updateTriggerRule(rule.id, { enabled: !rule.enabled });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to update");
    }
  }

  async function removeRule(rule: TriggerRule) {
    if (!window.confirm(`Delete trigger rule #${rule.id}?`)) return;
    try {
      await api.deleteTriggerRule(rule.id);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to delete");
    }
  }

  async function toggleWebhook(r: { id: number; full_name: string; webhook_registered: boolean }) {
    setError(null);
    try {
      if (r.webhook_registered) {
        await api.unregisterWebhook(r.id);
      } else {
        await api.registerWebhook(r.id);
      }
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "webhook action failed");
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between animate-fade-up">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-ink-100">Triggers</h1>
          <p className="mt-1 text-sm text-ink-500">
            Webhook-pushed automation: a GitHub event matches a rule and auto-starts reviewer/task
            work in real time. Webhooks are the preferred path (not polling).
          </p>
        </div>
        <button
          onClick={() => {
            setEditing(null);
            setShowForm(true);
          }}
          className="btn-primary"
        >
          + New rule
        </button>
      </header>

      {status && (
        <section className="surface p-5 animate-fade-up">
          <h2 className="panel-title mb-2">Webhook status</h2>
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span className={`rounded-full border px-2.5 py-0.5 text-xs ${
              status.reachable ? "border-green-900 text-green-400" : "border-red-900 text-red-400"
            }`}>
              {status.reachable ? "reachable" : "not exposed"}
            </span>
            <span className="font-mono text-xs text-ink-400">{status.url || "no webhook_url set — set it in Settings to enable delivery"}</span>
            {status.secret_set && <span className="text-xs text-ink-500">signature verified</span>}
          </div>
          <ul className="mt-3 divide-y divide-ink-800/70">
            {status.repos.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
                <span className="font-mono text-ink-200">{r.full_name}</span>
                <span className="text-[11px] text-ink-500">
                  {r.webhook_registered ? "registered" : "not registered"}
                </span>
                <button
                  onClick={() => toggleWebhook(r)}
                  className="btn-ghost ml-auto !px-2.5 !py-1 text-xs"
                >
                  {r.webhook_registered ? "Unregister" : "Register"}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {error && <p className="text-sm text-red-400">{error}</p>}

      {showForm && (
        <RuleForm
          repos={repos}
          agents={agents}
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
      )}

      <section className="surface animate-fade-up">
        <div className="border-b border-ink-800 px-4 py-3">
          <h2 className="panel-title">Trigger rules</h2>
        </div>
        {rules.length === 0 ? (
          <p className="px-4 py-6 text-sm text-ink-500">
            No rules yet — create one to auto-start work on a GitHub event.
          </p>
        ) : (
          <ul className="divide-y divide-ink-800/70">
            {rules.map((rule) => (
              <li key={rule.id} className="flex flex-wrap items-center gap-2 px-4 py-3 text-sm">
                <span className="font-mono text-ink-300">{rule.event}</span>
                <span className="rounded-full border border-syrup-500/40 bg-syrup-500/10 px-2 py-0.5 font-mono text-[11px] text-syrup-300">
                  {rule.action}
                </span>
                <span className="text-ink-500">{repoName(rule.repo_id)}</span>
                {rule.branch_filter && <span className="font-mono text-[11px] text-ink-500">branch: {rule.branch_filter}</span>}
                {rule.agent_ids.length > 0 && (
                  <span className="font-mono text-[11px] text-ink-500">agents: {rule.agent_ids.join(", ")}</span>
                )}
                {!rule.enabled && (
                  <span className="rounded-full border border-ink-800 px-2 py-0.5 text-[11px] text-ink-500">disabled</span>
                )}
                <span className="ml-auto flex gap-2">
                  <button onClick={() => toggleRule(rule)} className="btn-ghost !px-2 !py-1 text-xs">
                    {rule.enabled ? "Disable" : "Enable"}
                  </button>
                  <button
                    onClick={() => {
                      setEditing(rule);
                      setShowForm(true);
                    }}
                    className="btn-ghost !px-2 !py-1 text-xs"
                  >
                    Edit
                  </button>
                  <button onClick={() => removeRule(rule)} className="btn-ghost !px-2 !py-1 !text-red-400 text-xs">
                    Delete
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="surface animate-fade-up">
        <div className="border-b border-ink-800 px-4 py-3">
          <h2 className="panel-title">Delivery log</h2>
        </div>
        {deliveries.length === 0 ? (
          <p className="px-4 py-6 text-sm text-ink-500">No deliveries yet.</p>
        ) : (
          <ul className="divide-y divide-ink-800/70">
            {deliveries.map((d) => (
              <DeliveryRow key={d.id} d={d} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
