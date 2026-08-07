import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
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
        <p className="text-xs text-ink-600">Account</p>
        <p className="mt-0.5 font-mono text-sm text-ink-100">
          {account.login ?? "—"}
          {account.name !== "default" && (
            <span className="ml-2 rounded bg-ink-850 px-1.5 py-0.5 font-mono text-[10px] text-ink-400">
              {account.name}
            </span>
          )}
        </p>
      </div>
      <div>
        <p className="text-xs text-ink-600">Status</p>
        <p className={`mt-0.5 text-sm ${account.valid ? "text-ink-100" : "text-red-400"}`}>
          {account.valid ? "valid & authorized" : account.error ?? "invalid"}
        </p>
      </div>
      <div>
        <p className="text-xs text-ink-600">Token</p>
        <p className="mt-0.5 font-mono text-sm text-ink-300">
          {account.token_type ?? "unknown"} · {account.masked}
        </p>
      </div>
    </div>
  );
}

function TokenForm({ onStored }: { onStored: () => void }) {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!token.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.putGithubToken(token.trim());
      setToken("");
      onStored();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to store token");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="surface space-y-4 p-6">
      <div>
        <h2 className="panel-title">Connect your default GitHub account</h2>
        <p className="mt-1 text-sm text-ink-400">
          Paste a personal access token with <code className="font-mono text-syrup-300">repo</code>{" "}
          scope. Stored locally at <code className="font-mono text-ink-300">0600</code>; never
          leaves this machine.
        </p>
      </div>
      <input
        type="password"
        value={token}
        onChange={(e) => setToken(e.target.value)}
        placeholder="ghp_… / github_pat_…"
        className="field font-mono"
        autoComplete="off"
        spellCheck={false}
      />
      {error && <p className="text-xs text-red-400">{error}</p>}
      <div className="flex justify-end">
        <button type="submit" disabled={busy || !token.trim()} className="btn-primary">
          {busy ? "Validating…" : "Validate & store"}
        </button>
      </div>
    </form>
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
        <h2 className="panel-title">Add another GitHub account</h2>
        <p className="mt-1 text-sm text-ink-400">
          Each saved PAT becomes its own account — its repos appear below and are selectable when
          creating tasks.
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
        <button type="submit" disabled={busy || !name.trim() || !value.trim()} className="btn-primary">
          {busy ? "Adding…" : "Add account"}
        </button>
      </div>
      {error && <p className="text-xs text-red-400">{error}</p>}
    </form>
  );
}

export default function Github() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [repos, setRepos] = useState<GithubRepo[]>([]);
  const [connected, setConnected] = useState<Repo[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .getTokens()
      .then((t) => setAccounts(t.accounts ?? []))
      .catch(() => {});
    api
      .getGithubRepos()
      .then(setRepos)
      .catch((e: Error) => setError(e.message));
    api
      .getRepos()
      .then(setConnected)
      .catch(() => {});
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const reposByAccount = useMemo(() => {
    const map = new Map<string, GithubRepo[]>();
    for (const repo of repos) {
      const key = repo.account ?? "default";
      if (repo.error) continue; // per-account error is surfaced on the account card
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(repo);
    }
    return map;
  }, [repos]);

  const hasDefault = accounts.some((a) => a.is_default);
  const defaultAccount = accounts.find((a) => a.is_default);

  async function connect(account: string, fullName: string) {
    try {
      await api.connectRepo(fullName, account === "default" ? undefined : account);
      setConnected(await api.getRepos());
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to connect repo");
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
    try {
      await api.deleteToken(name);
      setError(null);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to remove account");
    }
  }

  async function prune() {
    try {
      const res = await api.pruneRepos();
      setConnected(await api.getRepos());
      if (res.removed.length === 0) setError("No deleted repos found — all connected repos still exist.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to prune repos");
    }
  }

  return (
    <div className="space-y-6">
      <header className="animate-fade-up">
        <h1 className="text-3xl font-bold tracking-tight text-ink-100">GitHub</h1>
        <p className="mt-1 text-sm text-ink-500">
          Every saved PAT is a separate account — each shows its own status and repositories.
        </p>
      </header>

      {(!hasDefault || (defaultAccount && !defaultAccount.valid)) && (
        <div className="animate-fade-up">
          <TokenForm onStored={load} />
        </div>
      )}

      {accounts.map((account) => (
        <section key={account.name} className="surface animate-fade-up">
          <div className="flex items-center gap-3 border-b border-ink-800 px-6 py-4">
            <span
              className={`h-2.5 w-2.5 rounded-full ${account.valid ? "bg-green-400" : "bg-red-400"}`}
            />
            <h2 className="panel-title">
              {account.login ?? account.name}
              {account.is_default && (
                <span className="ml-2 rounded bg-ink-850 px-1.5 py-0.5 font-mono text-[10px] text-ink-500">
                  default
                </span>
              )}
            </h2>
            {!account.is_default && (
              <button
                onClick={() => removeAccount(account.name)}
                className="ml-auto text-[11px] text-ink-500 transition-colors hover:text-red-300"
              >
                remove
              </button>
            )}
          </div>
          <div className="space-y-4 px-6 py-5">
            <AccountStatus account={account} />

            <div>
              <p className="mb-2 text-xs text-ink-600">Granted scopes</p>
              <div className="flex flex-wrap gap-1.5">
                {account.granted_scopes.length === 0 ? (
                  <span className="text-sm text-ink-600">none</span>
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
              </div>
            )}

            <div className="border-t border-ink-800 pt-3">
              <p className="mb-2 text-xs text-ink-600">
                Repositories ({reposByAccount.get(account.name)?.length ?? 0})
              </p>
              {(reposByAccount.get(account.name) ?? []).length === 0 && (
                <p className="text-sm text-ink-600">No repositories listed for this account.</p>
              )}
              <ul className="divide-y divide-ink-800/70">
                {(reposByAccount.get(account.name) ?? []).map((r) => {
                  const [owner, repo] = r.full_name.split("/");
                  const connectedRow = connected.find((c) => c.full_name === r.full_name);
                  return (
                    <li key={r.full_name} className="flex items-center gap-3 py-2.5">
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
                        <span className="ml-2 font-mono text-[11px] text-ink-600">
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
                          className="btn-ghost !px-3 !py-1 text-xs"
                        >
                          Connect
                        </button>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          </div>
        </section>
      ))}

      {accounts.length > 0 && (
        <div className="flex items-center justify-end gap-2">
          <button onClick={prune} className="btn-ghost !px-3 !py-1 text-xs">
            Prune deleted repos
          </button>
        </div>
      )}

      {error && <p className="text-sm text-red-400">{error}</p>}

      <AddAccountForm onAdded={load} />
    </div>
  );
}
