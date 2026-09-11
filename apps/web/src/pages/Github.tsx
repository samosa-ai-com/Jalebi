import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { EmptyState } from "../components/EmptyState";
import SearchableSelect from "../components/SearchableSelect";
import type { Account, GithubRepo, Repo } from "../types";

function ScopeChip({ scope }: { scope: string }) {
  return (
    <span className="rounded bg-ink-850 px-2 py-0.5 font-mono text-[11px] text-ink-300">
      {scope}
    </span>
  );
}

function AccountStatus({ account }: { account: Account }) {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      <div>
        <p className="text-xs text-ink-500">Account</p>
        <p className="mt-0.5 font-mono text-sm text-ink-100">
          {account.login ?? "—"}
          <span className="ml-2 rounded bg-ink-850 px-1.5 py-0.5 font-mono text-[10px] text-ink-400">
            {account.name}
          </span>
        </p>
      </div>
      <div>
        <p className="text-xs text-ink-500">Status</p>
        <p className={`mt-0.5 text-sm ${account.valid ? "text-ink-100" : "text-red-400"}`}>
          {account.valid ? "valid & authorized" : (account.error ?? "invalid")}
        </p>
      </div>
      <div>
        <p className="text-xs text-ink-500">Token</p>
        <p className="mt-0.5 font-mono text-sm text-ink-300">
          {account.token_type ?? "unknown"} · {account.masked}
        </p>
      </div>
    </div>
  );
}

function AddAccountForm({ onAdded }: { onAdded: () => void }) {
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !value.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.addToken(name.trim(), value.trim());
      setName("");
      setValue("");
      onAdded();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to add account");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={add} className="surface space-y-3 p-6 animate-fade-up">
      <div>
        <h2 className="panel-title">Add a GitHub account</h2>
        <p className="mt-1 text-sm text-ink-400">
          Each saved PAT is its own account — its repos appear below and are selectable when
          creating tasks. All accounts are equal; the one you pick for a task is the one used.
        </p>
        <p className="mt-2 text-xs leading-relaxed text-ink-500">
          Recommended: a <span className="font-mono">classic</span> token with the{" "}
          <span className="font-mono">repo</span> scope. It also covers reading GitHub Actions
          logs, which the reviewer and “Fix failed CI” use. A fine-grained token works too, but
          add <span className="font-mono">Actions: read</span> for CI logs (plus Contents, Pull
          requests, Issues, Metadata, and Commit statuses).
        </p>
      </div>
      <div className="grid gap-2 sm:grid-cols-[1fr_2fr_auto] sm:items-center">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="label (e.g. work, personal)"
          className="field font-mono"
          spellCheck={false}
        />
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          type="password"
          placeholder="ghp_…"
          className="field font-mono"
          autoComplete="off"
          spellCheck={false}
        />
        <button
          type="submit"
          disabled={busy || !name.trim() || !value.trim()}
          className="btn-primary"
        >
          {busy ? "Adding…" : "Add account"}
        </button>
      </div>
      {error && <p className="text-xs text-red-400">{error}</p>}
    </form>
  );
}

function UpdateTokenForm({ account, onUpdated }: { account: Account; onUpdated: () => void }) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!value.trim()) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await api.updateToken(account.name, value.trim());
      setValue("");
      if (res.previous_login && res.login && res.previous_login !== res.login) {
        setNotice(
          `Token updated. This account now authenticates as ${res.login} (was ${res.previous_login}).`
        );
      }
      onUpdated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to update token");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="mt-3 rounded-md border border-ink-800 bg-ink-900/40 p-3">
      <p className="text-xs text-ink-500">
        Replacing this token only swaps the credential — connected repos, tasks and history for{" "}
        <span className="font-mono text-ink-300">{account.name}</span> are kept intact.
      </p>
      <div className="mt-2 grid gap-2 sm:grid-cols-[1fr_auto] sm:items-center">
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          type="password"
          placeholder="new ghp_…"
          className="field font-mono"
          autoComplete="off"
          spellCheck={false}
        />
        <button
          type="submit"
          disabled={busy || !value.trim()}
          className="btn-primary !px-3 !py-1 text-xs"
        >
          {busy ? "Updating…" : "Update token"}
        </button>
      </div>
      {error && <p className="mt-2 text-xs text-red-400">{error}</p>}
      {notice && <p className="mt-2 text-xs text-ink-400">{notice}</p>}
    </form>
  );
}

const OPEN_ACCOUNTS_KEY = "jalebi-github-open-accounts";

function loadOpenAccounts(): Set<string> {
  try {
    const raw = localStorage.getItem(OPEN_ACCOUNTS_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    if (Array.isArray(parsed)) return new Set(parsed.filter((x) => typeof x === "string"));
  } catch {
    // Corrupt storage falls back to all-collapsed.
  }
  return new Set();
}

export default function Github() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [repos, setRepos] = useState<GithubRepo[]>([]);
  const [connected, setConnected] = useState<Repo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [updating, setUpdating] = useState<string | null>(null);
  const [loadingAccounts, setLoadingAccounts] = useState(true);
  const [loadingRepos, setLoadingRepos] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [connecting, setConnecting] = useState<Set<string>>(new Set());
  // Account sections are collapsible (collapsed by default); open state
  // persists in localStorage like the Settings sections.
  const [openAccounts, setOpenAccounts] = useState<Set<string>>(loadOpenAccounts);
  const [controls, setControls] = useState<
    Record<string, { q: string; status: "all" | "connected" | "not"; sort: "name" | "connected" }>
  >({});

  function controlsFor(account: string) {
    return controls[account] ?? { q: "", status: "all" as const, sort: "name" as const };
  }

  function setControl(
    account: string,
    patch: Partial<{ q: string; status: "all" | "connected" | "not"; sort: "name" | "connected" }>
  ) {
    setControls((prev) => ({ ...prev, [account]: { ...controlsFor(account), ...patch } }));
  }

  const load = useCallback(async () => {
    setLoadingAccounts(true);
    setLoadingRepos(true);
    try {
      const t = await api.getTokens();
      setAccounts(t.accounts ?? []);
    } catch {
      // Accounts fail silently (existing behavior); repos carry the error.
    } finally {
      setLoadingAccounts(false);
    }
    try {
      setRepos(await api.getGithubRepos());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "failed to list repositories");
    } finally {
      setLoadingRepos(false);
    }
    try {
      setConnected(await api.getRepos());
    } catch {
      // Connected list is best-effort; rows fall back to Connect buttons.
    }
  }, []);

  useEffect(() => {
    // Mount-time data fetch (not derived state): loading flags start true and
    // load() resolves them in finally blocks.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  function toggleAccount(name: string) {
    setOpenAccounts((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      try {
        localStorage.setItem(OPEN_ACCOUNTS_KEY, JSON.stringify([...next]));
      } catch {
        // Storage failures (private mode quota) just lose persistence.
      }
      return next;
    });
  }

  function setAllAccounts(open: boolean) {
    const next = open ? new Set(accounts.map((a) => a.name)) : new Set<string>();
    try {
      localStorage.setItem(OPEN_ACCOUNTS_KEY, JSON.stringify([...next]));
    } catch {
      // Storage failures just lose persistence.
    }
    setOpenAccounts(next);
  }

  async function refresh() {
    setRefreshing(true);
    try {
      await load();
    } finally {
      setRefreshing(false);
    }
  }

  async function retryAccount(account: string) {
    try {
      const list = await api.getGithubRepos(account);
      setRepos((prev) => [
        ...prev.filter((r) => r.account !== account && !(r.error && r.account === account)),
        ...list,
      ]);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to list repositories");
    }
  }

  const { reposByAccount, repoErrors } = useMemo(() => {
    const map = new Map<string, GithubRepo[]>();
    const errors = new Map<string, string>();
    for (const repo of repos) {
      const key = repo.account ?? "";
      if (repo.error) {
        // A failed account contributes an error entry — surface it on the
        // account card (previously these were silently dropped).
        errors.set(key, repo.error);
        continue;
      }
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(repo);
    }
    return { reposByAccount: map, repoErrors: errors };
  }, [repos]);

  function visibleRepos(account: string): GithubRepo[] {
    const c = controlsFor(account);
    const q = c.q.trim().toLowerCase();
    const connectedNames = new Set(connected.map((r) => r.full_name));
    const filtered = (reposByAccount.get(account) ?? []).filter((r) => {
      const isConnected = connectedNames.has(r.full_name);
      if (c.status === "connected" && !isConnected) return false;
      if (c.status === "not" && isConnected) return false;
      if (!q) return true;
      return r.full_name.toLowerCase().includes(q);
    });
    return [...filtered].sort((a, b) => {
      if (c.sort === "connected") {
        const ac = connectedNames.has(a.full_name) ? 0 : 1;
        const bc = connectedNames.has(b.full_name) ? 0 : 1;
        if (ac !== bc) return ac - bc;
      }
      return a.full_name.localeCompare(b.full_name);
    });
  }

  async function connect(account: string, fullName: string) {
    if (connecting.has(fullName)) return;
    setConnecting((prev) => new Set(prev).add(fullName));
    try {
      await api.connectRepo(fullName, account);
      setConnected(await api.getRepos());
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to connect repo");
    } finally {
      setConnecting((prev) => {
        const next = new Set(prev);
        next.delete(fullName);
        return next;
      });
    }
  }

  async function disconnect(id: number) {
    try {
      await api.disconnectRepo(id);
      setConnected(await api.getRepos());
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to disconnect repo");
    }
  }

  async function removeAccount(name: string) {
    const confirmMsg = `Remove account "${name}"?\n\nThis also deletes ${name}'s connected repos and all their tasks. This cannot be undone.`;
    if (!window.confirm(confirmMsg)) return;
    try {
      const res = await api.deleteToken(name);
      if (res.repos_affected.length > 0 || res.tasks_affected > 0) {
        window.alert(
          `Account removed. Deleted ${res.repos_affected.length} repo(s) and ${res.tasks_affected} task(s).`
        );
      }
      setError(null);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to remove account");
    }
  }

  async function prune() {
    setNotice(null);
    setError(null);
    try {
      const res = await api.pruneRepos();
      setConnected(await api.getRepos());
      if (res.removed.length === 0) {
        // A successful no-op is informational, not an error (F11).
        setNotice("No deleted repos found — all connected repos still exist.");
      } else if (res.removed.length === 1) {
        setNotice(`Removed ${res.removed.length} repo that no longer exists upstream.`);
      } else {
        setNotice(`Removed ${res.removed.length} repos that no longer exist upstream.`);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to prune repos");
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex items-start justify-between animate-fade-up">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-ink-100">GitHub</h1>
          <p className="mt-1 text-sm text-ink-500">
            Every saved PAT is a separate account — each shows its own status and repositories.
          </p>
        </div>
        <button onClick={refresh} disabled={refreshing} className="btn-ghost text-xs">
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </header>

      {loadingAccounts && accounts.length === 0 && (
        <p className="text-sm text-ink-500 animate-fade-up">Loading accounts…</p>
      )}

      {!loadingAccounts && accounts.length === 0 && (
        <EmptyState
          icon="🐙"
          title="No GitHub accounts yet"
          description="Add a personal access token below to connect repositories and run agent tasks."
          action={
            <button
              type="button"
              onClick={() => {
                const input = document.querySelector<HTMLInputElement>(
                  'input[placeholder*="label"]'
                );
                input?.focus();
                input?.scrollIntoView({ behavior: "smooth" });
              }}
              className="btn-primary"
            >
              Add an account below
            </button>
          }
        />
      )}

      {accounts.map((account) => {
        const open = openAccounts.has(account.name);
        return (
          <section key={account.name} className="surface animate-fade-up">
            <div className="flex items-center gap-3 border-b border-ink-800 px-6 py-4">
              <button
                onClick={() => toggleAccount(account.name)}
                aria-expanded={open}
                aria-label={`${open ? "Collapse" : "Expand"} account ${account.login ?? account.name}`}
                className="flex min-w-0 flex-1 items-center gap-3 text-left"
              >
                <span
                  className={`h-2.5 w-2.5 shrink-0 rounded-full ${account.valid ? "bg-green-400" : "bg-red-400"}`}
                />
                <span className="text-ink-500" aria-hidden="true">
                  {open ? "▾" : "▸"}
                </span>
                <h2 className="panel-title truncate">{account.login ?? account.name}</h2>
              </button>
              <div className="ml-auto flex shrink-0 items-center gap-3">
                <button
                  onClick={() => setUpdating(updating === account.name ? null : account.name)}
                  className="text-[11px] text-ink-500 transition-colors hover:text-syrup-300"
                >
                  {updating === account.name ? "cancel" : "update token"}
                </button>
                <button
                  onClick={() => removeAccount(account.name)}
                  className="text-[11px] text-ink-500 transition-colors hover:text-red-300"
                >
                  remove
                </button>
              </div>
            </div>
            {open && (
              <div className="space-y-4 px-6 py-5">
                {updating === account.name && (
                  <UpdateTokenForm account={account} onUpdated={load} />
                )}
                <AccountStatus account={account} />

                <div>
                  <p className="mb-2 text-xs text-ink-500">Granted scopes</p>
                  <div className="flex flex-wrap gap-1.5">
                    {account.granted_scopes.length === 0 ? (
                      <span className="text-sm text-ink-500">none</span>
                    ) : (
                      account.granted_scopes.map((s) => <ScopeChip key={s} scope={s} />)
                    )}
                  </div>
                </div>

                {account.missing_scopes.length > 0 && (
                  <div>
                    <p className="mb-2 text-xs text-red-400">Missing scopes</p>
                    <div className="flex flex-wrap gap-1.5">
                      {account.missing_scopes.map((s) => (
                        <span
                          key={s}
                          className="rounded bg-red-500/10 px-2 py-0.5 font-mono text-[11px] text-red-300"
                        >
                          {s}
                        </span>
                      ))}
                    </div>
                    <p className="mt-1.5 text-[11px] text-ink-500">
                      Without these, Jalebi can&apos;t review, comment, push, or report commit
                      statuses. A <span className="font-mono">classic</span> token needs only the{" "}
                      <span className="font-mono">repo</span> scope (it also covers reading
                      Actions logs); a fine-grained token needs the permissions here plus{" "}
                      <span className="font-mono">Actions: read</span> for CI logs. Create one at{" "}
                      <a
                        href="https://github.com/settings/tokens"
                        target="_blank"
                        rel="noreferrer"
                        className="link"
                      >
                        github.com/settings/tokens
                      </a>{" "}
                      then use “update token” above.
                    </p>
                  </div>
                )}

                <div className="border-t border-ink-800 pt-3">
                  <p className="mb-2 text-xs text-ink-500">
                    Repositories ({visibleRepos(account.name).length}
                    {visibleRepos(account.name).length !==
                      (reposByAccount.get(account.name) ?? []).length &&
                      ` of ${(reposByAccount.get(account.name) ?? []).length}`}
                    )
                  </p>
                  {repoErrors.get(account.name) && (
                    <p className="mb-2 text-xs text-red-400">
                      Couldn&apos;t list repositories: {repoErrors.get(account.name)}{" "}
                      <button onClick={() => retryAccount(account.name)} className="link text-xs">
                        Retry
                      </button>
                    </p>
                  )}
                  {loadingRepos &&
                  (reposByAccount.get(account.name) ?? []).length === 0 &&
                  !repoErrors.get(account.name) ? (
                    <p className="text-sm text-ink-500">Loading repositories…</p>
                  ) : visibleRepos(account.name).length === 0 ? (
                    <p className="text-sm text-ink-500">
                      {(reposByAccount.get(account.name) ?? []).length === 0
                        ? "No repositories listed for this account."
                        : "No repositories match the current search or filters."}
                    </p>
                  ) : (
                    <>
                      <div className="mb-2 flex flex-wrap items-center gap-2">
                        <input
                          value={controlsFor(account.name).q}
                          onChange={(e) => setControl(account.name, { q: e.target.value })}
                          placeholder="Search repositories…"
                          className="field max-w-55 !py-1 text-xs"
                        />
                        <SearchableSelect
                          label={`Sort repositories for ${account.name}`}
                          hideLabel
                          value={controlsFor(account.name).sort}
                          onChange={(v) =>
                            setControl(account.name, {
                              sort: v as "name" | "connected",
                            })
                          }
                          options={[
                            { value: "name", label: "Sort: name" },
                            { value: "connected", label: "Sort: connected first" },
                          ]}
                        />
                        {(["all", "connected", "not"] as const).map((s) => (
                          <button
                            key={s}
                            onClick={() => setControl(account.name, { status: s })}
                            className={`rounded-full border px-2 py-0.5 text-[11px] transition-colors ${
                              controlsFor(account.name).status === s
                                ? "border-syrup-500 text-syrup-300"
                                : "border-ink-800 text-ink-400 hover:text-ink-100"
                            }`}
                          >
                            {s === "all"
                              ? "All"
                              : s === "connected"
                                ? "Connected"
                                : "Not connected"}
                          </button>
                        ))}
                      </div>
                      <div className="max-h-[26rem] overflow-y-auto rounded-md border border-ink-800/60">
                        <ul className="divide-y divide-ink-800/70">
                          {visibleRepos(account.name).map((r) => {
                            const [owner, repo] = r.full_name.split("/");
                            const connectedRow = connected.find((c) => c.full_name === r.full_name);
                            const isConnecting = connecting.has(r.full_name);
                            return (
                              <li key={r.full_name} className="flex items-center gap-3 py-2.5 px-3">
                                <span className="h-2 w-2 shrink-0 rounded-full bg-chai-500" />
                                <span className="min-w-0 flex-1">
                                  <a
                                    href={r.html_url}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="font-mono text-sm text-ink-200 transition-colors hover:text-syrup-300"
                                  >
                                    <span className="text-ink-500">{owner}/</span>
                                    {repo}
                                  </a>
                                  <span className="ml-2 font-mono text-[11px] text-ink-500">
                                    {r.private ? "private" : "public"} · {r.default_branch ?? "—"}
                                  </span>
                                </span>
                                {connectedRow ? (
                                  <div className="flex items-center gap-2">
                                    <span className="rounded-full bg-green-500/10 px-3 py-1 font-mono text-[11px] text-green-300">
                                      connected
                                    </span>
                                    <button
                                      onClick={() => disconnect(connectedRow.id)}
                                      className="text-[11px] text-ink-500 transition-colors hover:text-red-300"
                                    >
                                      disconnect
                                    </button>
                                  </div>
                                ) : (
                                  <button
                                    onClick={() => connect(account.name, r.full_name)}
                                    disabled={isConnecting}
                                    className="btn-ghost !px-3 !py-1 text-xs"
                                  >
                                    {isConnecting ? "Connecting…" : "Connect"}
                                  </button>
                                )}
                              </li>
                            );
                          })}
                        </ul>
                      </div>
                    </>
                  )}
                </div>
              </div>
            )}
          </section>
        );
      })}

      {accounts.length > 0 && (
        <div className="flex items-center justify-end gap-2">
          <button onClick={() => setAllAccounts(true)} className="btn-ghost !px-3 !py-1 text-xs">
            Expand all
          </button>
          <button onClick={() => setAllAccounts(false)} className="btn-ghost !px-3 !py-1 text-xs">
            Collapse all
          </button>
          <button onClick={prune} className="btn-ghost !px-3 !py-1 text-xs">
            Prune deleted repos
          </button>
        </div>
      )}

      {error && <p className="text-sm text-red-400">{error}</p>}
      {notice && <p className="text-sm text-ink-400">{notice}</p>}

      <AddAccountForm onAdded={load} />
    </div>
  );
}
