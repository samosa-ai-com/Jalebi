import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import SearchableSelect from "../components/SearchableSelect";
import type { Repo } from "../types";

function timeAgo(iso: string | null): string {
  if (!iso) return "never";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "never";
  const m = Math.floor((Date.now() - t) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function RepoRow({
  repo,
  toggling,
  onToggleStatuses,
  onDisconnect,
}: {
  repo: Repo;
  toggling: boolean;
  onToggleStatuses: () => void;
  onDisconnect: () => void;
}) {
  const [owner, name] = repo.full_name.split("/");
  return (
    <li className="flex items-center gap-3 px-6 py-3.5">
      <span className="h-2 w-2 shrink-0 rounded-full bg-syrup-400" />
      <span className="min-w-0 flex-1">
        <span className="font-mono text-sm text-ink-200">
          <span className="text-ink-500">{owner}/</span>
          {name}
        </span>
        <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 font-mono text-[11px] text-ink-600">
          <span>{repo.default_branch}</span>
          {repo.pat_name && <span title="Bound GitHub account">@{repo.pat_name}</span>}
          <span
            title={
              repo.webhook_registered
                ? "Webhook registered — events push in real time"
                : "No webhook — register one on the Triggers page for real-time events"
            }
            className={repo.webhook_registered ? "text-green-400" : "text-ink-600"}
          >
            webhook: {repo.webhook_registered ? "on" : "off"}
          </span>
          {repo.poll_fallback && <span title="Polling fallback enabled">polling</span>}
          <span title="Last poller check">checked {timeAgo(repo.last_checked_at)}</span>
        </span>
      </span>
      <span className="hidden font-mono text-[11px] text-ink-600 sm:block">#{repo.id}</span>
      <button
        type="button"
        title={
          repo.check_runs_enabled
            ? "Commit statuses enabled (PRD F15)"
            : "Report commit statuses on PRs"
        }
        onClick={onToggleStatuses}
        disabled={toggling}
        className={`text-[11px] transition-colors ${
          repo.check_runs_enabled
            ? "text-green-400 hover:text-green-300"
            : "text-ink-500 hover:text-ink-300"
        }`}
      >
        {toggling ? "…" : repo.check_runs_enabled ? "statuses: on" : "statuses: off"}
      </button>
      <button
        onClick={onDisconnect}
        className="text-[11px] text-ink-500 transition-colors hover:text-red-300"
      >
        disconnect
      </button>
    </li>
  );
}

type SortKey = "name" | "checked";

export default function Repos() {
  const [repos, setRepos] = useState<Repo[]>([]);
  const [disconnected, setDisconnected] = useState<Repo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [toggling, setToggling] = useState<Set<number>>(new Set());
  const [reconnecting, setReconnecting] = useState<Set<number>>(new Set());
  const [query, setQuery] = useState("");
  const [accountFilter, setAccountFilter] = useState<string>("all");
  const [sort, setSort] = useState<SortKey>("name");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [connectedRows, allRows] = await Promise.all([api.getRepos(), api.getRepos(true)]);
      setRepos(connectedRows);
      setDisconnected(allRows.filter((r) => !r.connected));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to list repos");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Mount-time fetch (not derived state).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  async function refresh() {
    setRefreshing(true);
    try {
      await load();
    } finally {
      setRefreshing(false);
    }
  }

  async function toggleStatuses(repo: Repo) {
    if (toggling.has(repo.id)) return;
    setToggling((prev) => new Set(prev).add(repo.id));
    try {
      await api.updateRepo(repo.id, { check_runs_enabled: !repo.check_runs_enabled });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "toggle failed");
    } finally {
      setToggling((prev) => {
        const next = new Set(prev);
        next.delete(repo.id);
        return next;
      });
    }
  }

  async function disconnect(id: number) {
    try {
      await api.disconnectRepo(id);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to disconnect");
    }
  }

  async function reconnect(id: number) {
    if (reconnecting.has(id)) return;
    setReconnecting((prev) => new Set(prev).add(id));
    try {
      await api.reconnectRepo(id);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to reconnect");
    } finally {
      setReconnecting((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  }

  const accounts = useMemo(() => {
    const names = new Set<string>();
    [...repos, ...disconnected].forEach((r) => {
      if (r.pat_name) names.add(r.pat_name);
    });
    return [...names].sort();
  }, [repos, disconnected]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = repos.filter((r) => {
      if (accountFilter !== "all" && r.pat_name !== accountFilter) return false;
      if (!q) return true;
      return r.full_name.toLowerCase().includes(q);
    });
    return [...filtered].sort((a, b) => {
      if (sort === "checked")
        return (b.last_checked_at ?? "").localeCompare(a.last_checked_at ?? "");
      return a.full_name.localeCompare(b.full_name);
    });
  }, [repos, query, accountFilter, sort]);

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between animate-fade-up">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-ink-100">Repos</h1>
          <p className="mt-1 text-sm text-ink-500">
            The connected repositories the agent can open worktrees in. Connect new ones from the
            GitHub page.
          </p>
        </div>
        <button onClick={refresh} disabled={refreshing} className="btn-ghost text-xs">
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </header>

      {error && <p className="text-sm text-red-400">{error}</p>}

      <div className="flex flex-wrap items-center gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search repositories…"
          className="field max-w-xs !py-1.5 text-sm"
        />
        <SearchableSelect
          label="Filter by account"
          hideLabel
          value={accountFilter}
          onChange={setAccountFilter}
          options={[
            { value: "all", label: "All accounts" },
            ...accounts.map((a) => ({ value: a, label: `@${a}` })),
          ]}
        />
        <SearchableSelect
          label="Sort repos"
          hideLabel
          value={sort}
          onChange={(v) => setSort(v as SortKey)}
          options={[
            { value: "name", label: "Sort: name" },
            { value: "checked", label: "Sort: recently checked" },
          ]}
        />
      </div>

      <section className="surface animate-fade-up" style={{ animationDelay: "0.1s" }}>
        <div className="border-b border-ink-800 px-6 py-4">
          <h2 className="panel-title">
            Connected · {visible.length}
            {visible.length !== repos.length && ` of ${repos.length}`}
          </h2>
        </div>
        {loading && repos.length === 0 ? (
          <div className="px-6 py-10 text-center text-sm text-ink-600">Loading repositories…</div>
        ) : visible.length === 0 ? (
          <div className="px-6 py-10 text-center text-sm text-ink-600">
            {repos.length === 0 ? (
              <>
                Nothing connected yet. Pick one from the{" "}
                <Link to="/github" className="link">
                  GitHub page
                </Link>
                .
              </>
            ) : (
              "No repositories match the current search or filters."
            )}
          </div>
        ) : (
          <ul className="divide-y divide-ink-800/70">
            {visible.map((r) => (
              <RepoRow
                key={r.id}
                repo={r}
                toggling={toggling.has(r.id)}
                onToggleStatuses={() => toggleStatuses(r)}
                onDisconnect={() => disconnect(r.id)}
              />
            ))}
          </ul>
        )}
      </section>

      {disconnected.length > 0 && (
        <section className="surface animate-fade-up">
          <div className="border-b border-ink-800 px-6 py-4">
            <h2 className="panel-title">Disconnected · {disconnected.length}</h2>
          </div>
          <ul className="divide-y divide-ink-800/70">
            {disconnected.map((r) => {
              const [owner, name] = r.full_name.split("/");
              const busy = reconnecting.has(r.id);
              return (
                <li key={r.id} className="flex items-center gap-3 px-6 py-3.5">
                  <span className="h-2 w-2 shrink-0 rounded-full bg-ink-700" />
                  <span className="min-w-0 flex-1 font-mono text-sm text-ink-500">
                    <span className="text-ink-600">{owner}/</span>
                    {name}
                  </span>
                  {r.pat_name && (
                    <span className="font-mono text-[11px] text-ink-600">@{r.pat_name}</span>
                  )}
                  <button
                    onClick={() => reconnect(r.id)}
                    disabled={busy}
                    className="btn-ghost !px-3 !py-1 text-xs"
                  >
                    {busy ? "Reconnecting…" : "Reconnect"}
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </div>
  );
}
