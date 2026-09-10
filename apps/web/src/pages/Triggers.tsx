import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { EmptyState } from "../components/EmptyState";
import SearchableSelect from "../components/SearchableSelect";
import type {
  CatalogAgent,
  DeliveryRuleResult,
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

const ACTION_HELP: Record<string, string> = {
  start_review:
    "Each selected reviewer starts its own review task immediately. Requires at least one reviewer agent.",
  triage_issue:
    "An issue_fix task is opened for the issue (your prompt, or a default fix prompt). The first selected agent runs it; none = default agent.",
  create_task:
    "A freeform task is created with your prompt. The first selected agent runs it; none = default agent.",
  rerun_review:
    "Re-enqueues the PR's existing reviewer tasks for a fresh pass (terminal ones only). Prompt and agents are ignored.",
};

function AgentChips({
  agents,
  selected,
  onToggle,
}: {
  agents: CatalogAgent[];
  selected: string[];
  onToggle: (id: string) => void;
}) {
  if (agents.length === 0) {
    return (
      <span className="text-xs text-ink-500">No agents yet — create one on the Agents page.</span>
    );
  }
  return (
    <div className="flex flex-wrap gap-2">
      {agents.map((a) => {
        const checked = selected.includes(a.id);
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
              onChange={() => onToggle(a.id)}
              className="hidden"
            />
            {a.name} ({a.id})
          </label>
        );
      })}
    </div>
  );
}

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

  const reviewers = agents.filter((a) => a.kind === "reviewer" && a.enabled);
  const actionable = agents.filter((a) => a.enabled);
  const needsAgents = action === "start_review";
  const needsPrompt = action === "triage_issue" || action === "create_task";
  const noRepos = repos.length === 0;

  function validationError(): string | null {
    if (noRepos) return "Connect a repo first (Repos page) — a rule needs a repo to bind to.";
    if (needsAgents && agentIds.length === 0)
      return "start_review needs at least one reviewer agent.";
    if (needsPrompt && !instructions.trim())
      return "This action needs a prompt — it becomes the task the webhook creates.";
    return null;
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const problem = validationError();
    if (problem) {
      setMsg({ kind: "err", text: problem });
      return;
    }
    setBusy(true);
    setMsg(null);
    // Explicit null clears an optional field on edit (omitted keys are left
    // alone by the API); on create null behaves like unset.
    const body = {
      event,
      action,
      branch_filter: branchFilter.trim() || null,
      author_filter: authorFilter.trim() || null,
      label_filter: labels
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
      agent_ids: agentIds,
      custom_instructions: instructions.trim() || null,
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
        <div>
          <SearchableSelect
            label="Repo"
            value={repoId}
            onChange={(v) => setRepoId(Number(v))}
            disabled={!!editing || noRepos}
            options={repos.map((r) => ({ value: String(r.id), label: r.full_name }))}
          />
          {noRepos && (
            <span className="mt-1 block text-xs text-amber-400">
              No connected repos — connect one on the Repos page first.
            </span>
          )}
        </div>
        <SearchableSelect
          label="Event"
          value={event}
          onChange={setEvent}
          options={EVENTS}
        />
        <SearchableSelect
          label="Action"
          value={action}
          onChange={setAction}
          options={ACTIONS}
        />
      </div>
      <p className="text-xs leading-relaxed text-ink-500">{ACTION_HELP[action]}</p>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-ink-400">
            Branch filter (optional)
          </span>
          <input
            value={branchFilter}
            onChange={(e) => setBranchFilter(e.target.value)}
            placeholder="main"
            className="field font-mono"
          />
          <span className="mt-1 block text-[11px] text-ink-600">
            Matches the head or base branch (for push: the pushed ref). Clearing a saved filter
            removes it.
          </span>
        </label>
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-ink-400">
            Author filter (optional)
          </span>
          <input
            value={authorFilter}
            onChange={(e) => setAuthorFilter(e.target.value)}
            placeholder="octocat"
            className="field font-mono"
          />
          <span className="mt-1 block text-[11px] text-ink-600">
            PR/issue author login. Needs a PR or issue payload — never matches push.
          </span>
        </label>
      </div>

      <label className="block">
        <span className="mb-1.5 block text-xs font-medium text-ink-400">
          Label filter (comma-separated, optional)
        </span>
        <input
          value={labels}
          onChange={(e) => setLabels(e.target.value)}
          placeholder="bug, frontend"
          className="field"
        />
        <span className="mt-1 block text-[11px] text-ink-600">
          All listed labels must be present. Needs a PR or issue payload — never matches push.
        </span>
      </label>

      {needsAgents && (
        <fieldset>
          <legend className="mb-1.5 block text-xs font-medium text-ink-400">
            Reviewers (catalog agents, kind reviewer) — required
          </legend>
          <AgentChips
            agents={reviewers}
            selected={agentIds}
            onToggle={(id) =>
              setAgentIds((prev) =>
                prev.includes(id) ? prev.filter((n) => n !== id) : [...prev, id]
              )
            }
          />
        </fieldset>
      )}

      {(action === "triage_issue" || action === "create_task") && (
        <fieldset>
          <legend className="mb-1.5 block text-xs font-medium text-ink-400">
            Agent (optional — first selected runs the task, none = default agent)
          </legend>
          <AgentChips
            agents={actionable}
            selected={agentIds}
            onToggle={(id) =>
              setAgentIds((prev) =>
                prev.includes(id) ? prev.filter((n) => n !== id) : [...prev, id]
              )
            }
          />
        </fieldset>
      )}

      {needsPrompt && (
        <label className="block">
          <span className="mb-1.5 block text-xs font-medium text-ink-400">
            Custom instructions (task prompt) — required
          </span>
          <textarea
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            rows={2}
            className="field resize-y"
          />
        </label>
      )}

      <label className="flex items-center gap-2 text-sm text-ink-300">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
          className="h-4 w-4 rounded border-ink-700 bg-ink-900"
        />
        Enabled
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

function workSummary(rule: DeliveryRuleResult): string {
  const real = rule.work.filter((w) => w.type !== "error");
  const errs = rule.work.filter((w) => w.type === "error");
  if (real.length > 0) {
    const tasks = real.map((w) => w.task_id).filter((t) => t !== undefined);
    return tasks.length > 0
      ? `created task${tasks.length > 1 ? "s" : ""} ${tasks.join(", ")}`
      : "dispatched";
  }
  if (errs.length > 0) return errs[0].error || "error";
  return "no work";
}

function DeliveryRow({ d, onReplayed }: { d: EventDelivery; onReplayed: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<string | null>(null);

  const rules = d.result?.rules ?? [];
  const failedError =
    d.status === "failed" && rules.some((r) => r.work.some((w) => w.type === "error"));

  async function replay() {
    if (busy) return;
    setBusy(true);
    setOutcome(null);
    try {
      const res = await api.replayDelivery(d.id);
      const fired = res.results.filter((r) => !r.note);
      const skipped = res.results.filter((r) => r.note);
      const bits: string[] = [];
      if (fired.length > 0)
        bits.push(`dispatched ${fired.length} rule${fired.length > 1 ? "s" : ""}`);
      if (skipped.length > 0) bits.push(`${skipped.length} already dispatched — skipped`);
      setOutcome(bits.join("; ") || "replayed, nothing matched");
      onReplayed();
    } catch (err) {
      setOutcome(err instanceof Error ? err.message : "replay failed");
    } finally {
      setBusy(false);
    }
  }

  const when = new Date(d.received_at);
  const whenLabel = Number.isNaN(when.getTime())
    ? d.received_at
    : `${when.toLocaleDateString()} ${when.toLocaleTimeString()}`;

  return (
    <li className="py-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={() => setOpen((v) => !v)}
          className="flex flex-wrap items-center gap-2 text-left"
          aria-expanded={open}
        >
          <span
            className={`rounded-full border px-2 py-0.5 font-mono ${
              d.status === "matched"
                ? "border-green-900 text-green-400"
                : d.status === "failed"
                  ? "border-red-900 text-red-400"
                  : "border-ink-800 text-ink-500"
            }`}
          >
            {d.status}
          </span>
          {d.status === "failed" && (
            <span className="text-[11px] text-ink-500">
              {failedError ? "· error" : "· no work"}
            </span>
          )}
          <span className="font-mono text-ink-300">{d.event}</span>
          {d.action && <span className="text-ink-500">· {d.action}</span>}
          <span className="text-ink-500">{d.repo_full_name ?? "—"}</span>
          <span className="font-mono text-ink-600" title={d.received_at}>
            {whenLabel}
          </span>
          <span className="text-ink-600">{open ? "▾" : "▸"}</span>
        </button>
        <span className="ml-auto flex items-center gap-2">
          {outcome && <span className="text-ink-500">{outcome}</span>}
          <button
            onClick={replay}
            disabled={busy}
            className="btn-ghost !px-2 !py-0.5 disabled:opacity-50"
          >
            {busy ? "replaying…" : "replay"}
          </button>
        </span>
      </div>
      {open && (
        <div className="mt-2 space-y-2 rounded border border-ink-800 bg-ink-900/40 p-3">
          <div className="font-mono text-[11px] text-ink-500" title="GitHub delivery id">
            delivery {d.github_delivery_id}
            {d.result?.reason && <span> · {d.result.reason}</span>}
          </div>
          {rules.length === 0 && (
            <p className="text-[11px] text-ink-500">No rule outcomes recorded.</p>
          )}
          {rules.map((r) => (
            <div key={r.rule_id} className="text-[11px]">
              <span className="font-mono text-ink-300">
                rule #{r.rule_id} · {r.action}
              </span>
              <span className="ml-2 text-ink-500">{workSummary(r)}</span>
              {r.note && <span className="ml-2 text-ink-600">({r.note})</span>}
              <ul className="ml-4 mt-1 space-y-0.5">
                {r.work.map((w, i) => (
                  <li key={i} className={w.type === "error" ? "text-red-400" : "text-ink-400"}>
                    {w.type === "error" ? (
                      <>error: {w.error}</>
                    ) : w.task_id !== undefined ? (
                      <Link to={`/tasks/${w.task_id}`} className="text-syrup-300 hover:underline">
                        {w.type} → task #{w.task_id}
                      </Link>
                    ) : (
                      <>{w.type}</>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
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
  const [loadErrors, setLoadErrors] = useState<string[]>([]);
  const [statusFilter, setStatusFilter] = useState("all");
  const [logQuery, setLogQuery] = useState("");

  function load() {
    api
      .getTriggerRules()
      .then(setRules)
      .catch((e) => setError(e.message));
    api
      .getDeliveries()
      .then(setDeliveries)
      .catch((e) =>
        setLoadErrors((p) =>
          p.includes(`deliveries: ${e.message}`) ? p : [...p, `deliveries: ${e.message}`]
        )
      );
    api
      .getWebhookStatus()
      .then(setStatus)
      .catch((e) =>
        setLoadErrors((p) =>
          p.includes(`webhook status: ${e.message}`) ? p : [...p, `webhook status: ${e.message}`]
        )
      );
    api
      .getRepos()
      .then(setRepos)
      .catch((e) =>
        setLoadErrors((p) =>
          p.includes(`repos: ${e.message}`) ? p : [...p, `repos: ${e.message}`]
        )
      );
    api
      .getAgents(true)
      .then(setAgents)
      .catch((e) =>
        setLoadErrors((p) =>
          p.includes(`agents: ${e.message}`) ? p : [...p, `agents: ${e.message}`]
        )
      );
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

  const visibleDeliveries = deliveries.filter((d) => {
    if (statusFilter !== "all" && d.status !== statusFilter) return false;
    const q = logQuery.trim().toLowerCase();
    if (!q) return true;
    return (
      d.event.toLowerCase().includes(q) ||
      (d.repo_full_name ?? "").toLowerCase().includes(q) ||
      (d.action ?? "").toLowerCase().includes(q)
    );
  });

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

      {status ? (
        <section className="surface p-5 animate-fade-up">
          <h2 className="panel-title mb-2">Webhook status</h2>
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span
              className={`rounded-full border px-2.5 py-0.5 text-xs ${
                status.reachable ? "border-green-900 text-green-400" : "border-red-900 text-red-400"
              }`}
            >
              {status.reachable ? "reachable" : "not exposed"}
            </span>
            <span className="font-mono text-xs text-ink-400">
              {status.url || "no webhook_url set — set it in Settings to enable delivery"}
            </span>
            {status.secret_set && <span className="text-xs text-ink-500">signature verified</span>}
          </div>
          {status.url && !status.secret_set && (
            <p className="mt-2 text-xs leading-relaxed text-amber-400">
              No webhook secret set — deliveries are unsigned, so anyone who discovers the tunnel
              URL can forge events and trigger tasks. Set one in Settings → Webhooks.
            </p>
          )}
          <ul className="mt-3 divide-y divide-ink-800/70">
            {status.repos.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
                <span className="font-mono text-ink-200">{r.full_name}</span>
                <span className="text-[11px] text-ink-500">
                  {r.webhook_registered ? "registered" : "not registered"}
                </span>
                <button
                  onClick={() => toggleWebhook(r)}
                  disabled={!status.url}
                  title={!status.url ? "Set webhook_url in Settings first" : undefined}
                  className="btn-ghost ml-auto !px-2.5 !py-1 text-xs disabled:opacity-50"
                >
                  {r.webhook_registered ? "Unregister" : "Register"}
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : (
        loadErrors.some((e) => e.startsWith("webhook status")) && (
          <p className="text-sm text-red-400">
            Webhook status failed to load — registration controls unavailable.
          </p>
        )
      )}

      {error && <p className="text-sm text-red-400">{error}</p>}
      {loadErrors
        .filter((e) => !e.startsWith("webhook status"))
        .map((e) => (
          <p key={e} className="text-xs text-amber-400">
            {e}
          </p>
        ))}

      {showForm && (
        <RuleForm
          key={editing?.id ?? "new"}
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
          <div className="p-4">
            <EmptyState
              icon="⚡"
              title="No trigger rules yet"
              description="Create a rule to auto-start agent work when GitHub events arrive."
              action={
                <button
                  type="button"
                  onClick={() => {
                    setEditing(null);
                    setShowForm(true);
                  }}
                  className="btn-primary"
                >
                  Create rule
                </button>
              }
            />
          </div>
        ) : (
          <ul className="max-h-96 divide-y divide-ink-800/70 overflow-y-auto">
            {rules.map((rule) => (
              <li key={rule.id} className="flex flex-wrap items-center gap-2 px-4 py-3 text-sm">
                <span className="font-mono text-ink-300">{rule.event}</span>
                <span className="rounded-full border border-syrup-500/40 bg-syrup-500/10 px-2 py-0.5 font-mono text-[11px] text-syrup-300">
                  {rule.action}
                </span>
                <span className="text-ink-500">{repoName(rule.repo_id)}</span>
                {rule.branch_filter && (
                  <span className="rounded-full border border-ink-800 px-2 py-0.5 font-mono text-[11px] text-ink-400">
                    branch: {rule.branch_filter}
                  </span>
                )}
                {rule.author_filter && (
                  <span className="rounded-full border border-ink-800 px-2 py-0.5 font-mono text-[11px] text-ink-400">
                    by {rule.author_filter}
                  </span>
                )}
                {rule.label_filter.length > 0 && (
                  <span className="rounded-full border border-ink-800 px-2 py-0.5 font-mono text-[11px] text-ink-400">
                    labels: {rule.label_filter.join(", ")}
                  </span>
                )}
                {rule.agent_ids.length > 0 && (
                  <span className="font-mono text-[11px] text-ink-500">
                    agents: {rule.agent_ids.join(", ")}
                  </span>
                )}
                {rule.custom_instructions && (
                  <span
                    title={rule.custom_instructions}
                    className="rounded-full border border-ink-800 px-2 py-0.5 text-[11px] text-ink-500"
                  >
                    has prompt
                  </span>
                )}
                {!rule.enabled && (
                  <span className="rounded-full border border-ink-800 px-2 py-0.5 text-[11px] text-ink-500">
                    disabled
                  </span>
                )}
                <span className="ml-auto flex gap-2">
                  <button
                    onClick={() => toggleRule(rule)}
                    className="btn-ghost !px-2 !py-1 text-xs"
                  >
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
                  <button
                    onClick={() => removeRule(rule)}
                    className="btn-ghost !px-2 !py-1 !text-red-400 text-xs"
                  >
                    Delete
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="surface animate-fade-up">
        <div className="flex flex-wrap items-center gap-2 border-b border-ink-800 px-4 py-3">
          <h2 className="panel-title">Delivery log</h2>
          <span className="ml-auto flex items-center gap-2">
            <SearchableSelect
              label="Filter by status"
              hideLabel
              value={statusFilter}
              onChange={setStatusFilter}
              options={["all", "matched", "failed", "ignored"].map((s) => ({
                value: s,
                label: s === "all" ? "all statuses" : s,
              }))}
            />
            <input
              value={logQuery}
              onChange={(e) => setLogQuery(e.target.value)}
              placeholder="filter event / repo…"
              className="field !w-44 !py-1 text-xs"
              aria-label="Filter deliveries"
            />
          </span>
        </div>
        {deliveries.length === 0 ? (
          <p className="px-4 py-6 text-sm text-ink-500">No deliveries yet.</p>
        ) : visibleDeliveries.length === 0 ? (
          <p className="px-4 py-6 text-sm text-ink-500">No deliveries match the filter.</p>
        ) : (
          <ul className="max-h-96 divide-y divide-ink-800/70 overflow-y-auto px-4">
            {visibleDeliveries.map((d) => (
              <DeliveryRow key={d.id} d={d} onReplayed={load} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
