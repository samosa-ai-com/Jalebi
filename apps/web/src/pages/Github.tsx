import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { GithubRepo, Repo, TokenItem, TokenInfo } from "../types";

function ScopeChip({ scope }: { scope: string }) {
  return (
    <span className="rounded bg-ink-850 px-2 py-0.5 font-mono text-[11px] text-ink-300">
      {scope}
    </span>
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
        <h2 className="panel-title">Connect your GitHub account</h2>
        <p className="mt-1 text-sm text-ink-400">
          Paste a personal access token with <code className="font-mono text-syrup-300">repo</code>{" "}
          scope. It&apos;s stored locally at <code className="font-mono text-ink-300">0600</code> and
          never leaves this machine.
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

function TokensManager({
  tokens,
  onChanged,
}: {
  tokens: TokenItem[];
  onChanged: () => void;
}) {
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
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to add token");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="surface p-5 animate-fade-up">
      <h2 className="panel-title mb-2">Saved PATs</h2>
      <p className="mb-4 text-xs text-ink-500">
        Multiple tokens can be stored here and picked per task (task form / follow-up). The{" "}
        <span className="text-ink-300">Default</span> option uses your primary token.
      </p>
      {tokens.length === 0 && (
        <p className="mb-4 text-sm text-ink-600">No named tokens yet.</p>
      )}
      <ul className="mb-4 space-y-1.5">
        {tokens.map((t) => (
          <li key={t.name} className="flex items-center gap-2 text-sm">
            <span className="font-mono text-ink-200">{t.name}</span>
            <span className="font-mono text-[11px] text-ink-500">{t.masked}</span>
            <button
              onClick={() => api.deleteToken(t.name).then(onChanged).catch(() => {})}
              className="ml-auto text-[11px] text-red-400 hover:text-red-300"
            >
              remove
            </button>
          </li>
        ))}
      </ul>
      <form onSubmit={add} className="grid gap-2 sm:grid-cols-[1fr_2fr_auto] sm:items-center">
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
          {busy ? "Adding…" : "Add token"}
        </button>
      </form>
      {error && <p className="mt-2 text-xs text-red-400">{error}</p>}
    </section>
  );
}

export default function Github() {
  const [info, setInfo] = useState<TokenInfo | null>(null);
  const [hasToken, setHasToken] = useState(true);
  const [repos, setRepos] = useState<GithubRepo[]>([]);
  const [connected, setConnected] = useState<Repo[]>([]);
  const [tokens, setTokens] = useState<TokenItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loadingRepos, setLoadingRepos] = useState(false);

  const loadRepos = useCallback(() => {
    setLoadingRepos(true);
    api
      .getGithubRepos()
      .then(setRepos)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoadingRepos(false));
  }, []);

  const loadTokens = useCallback(() => {
    api
      .getTokens()
      .then((t) => setTokens(t.items ?? []))
      .catch(() => {});
  }, []);

  const load = useCallback(() => {
    api
      .getGithubStatus()
      .then((i) => {
        setInfo(i);
        setHasToken(true);
        setError(null);
        if (i.valid) loadRepos();
      })
      .catch((e: Error) => {
        if (e.message.includes("no GitHub token")) {
          setHasToken(false);
          setInfo(null);
        } else {
          setError(e.message);
        }
      });
    api
      .getRepos()
      .then(setConnected)
      .catch(() => {});
    loadTokens();
  }, [loadRepos, loadTokens]);

  useEffect(() => {
    load();
  }, [load]);

  const connectedNames = new Set(connected.map((r) => r.full_name));

  async function connect(fullName: string) {
    try {
      await api.connectRepo(fullName);
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
          API status, credentials, and the repos the agent can work on.
        </p>
      </header>

      {(!hasToken || (info && !info.valid)) && (
        <div className="animate-fade-up" style={{ animationDelay: "0.05s" }}>
          <TokenForm
            onStored={() => {
              load();
              loadRepos();
            }}
          />
        </div>
      )}

      {info && (
        <section
          className="surface animate-fade-up"
          style={{ animationDelay: "0.05s" }}
        >
          <div className="flex items-center gap-3 border-b border-ink-800 px-6 py-4">
            <span
              className={`h-2.5 w-2.5 rounded-full ${
                info.valid ? "bg-green-400" : "bg-red-400"
              } ${info.valid ? "animate-pulse-dot" : ""}`}
            />
            <h2 className="panel-title">Connection status</h2>
            <span className="ml-auto font-mono text-[11px] text-ink-600">
              {info.token_type ?? "unknown type"}
            </span>
          </div>
          <div className="space-y-4 px-6 py-5">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div>
                <p className="text-xs text-ink-600">Account</p>
                <p className="mt-0.5 font-mono text-sm text-ink-100">{info.login ?? "—"}</p>
              </div>
              <div>
                <p className="text-xs text-ink-600">Status</p>
                <p className="mt-0.5 text-sm text-ink-100">
                  {info.valid ? "valid & authorized" : info.error ?? "invalid"}
                </p>
              </div>
              {info.note && (
                <div>
                  <p className="text-xs text-ink-600">Note</p>
                  <p className="mt-0.5 text-sm text-ink-300">{info.note}</p>
                </div>
              )}
            </div>

            <div>
              <p className="mb-2 text-xs text-ink-600">Granted scopes</p>
              <div className="flex flex-wrap gap-1.5">
                {info.granted_scopes.length === 0 ? (
                  <span className="text-sm text-ink-600">none</span>
                ) : (
                  info.granted_scopes.map((s) => <ScopeChip key={s} scope={s} />)
                )}
              </div>
            </div>

            {info.missing_scopes.length > 0 && (
              <div>
                <p className="mb-2 text-xs text-red-400">Missing scopes</p>
                <div className="flex flex-wrap gap-1.5">
                  {info.missing_scopes.map((s) => (
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
          </div>
        </section>
      )}

      {error && <p className="text-sm text-red-400">{error}</p>}

      {hasToken && (
        <TokensManager
          tokens={tokens}
          onChanged={() => {
            loadTokens();
          }}
        />
      )}

      {hasToken && (
        <section
          className="surface animate-fade-up"
          style={{ animationDelay: "0.1s" }}
        >
          <div className="flex items-center gap-3 border-b border-ink-800 px-6 py-4">
            <h2 className="panel-title">Your GitHub repositories</h2>
            <button onClick={prune} className="btn-ghost ml-auto !px-3 !py-1 text-xs">
              Prune deleted
            </button>
            <button onClick={loadRepos} className="btn-ghost !px-3 !py-1 text-xs">
              Refresh
            </button>
          </div>
          {repos.length === 0 && !loadingRepos && (
            <div className="px-6 py-8 text-center text-sm text-ink-600">
              Repos not loaded yet — hit <span className="font-mono text-ink-400">Refresh</span> to
              pull the account&apos;s repositories.
            </div>
          )}
          {loadingRepos && (
            <div className="px-6 py-8 text-center text-sm text-ink-500">Loading repos…</div>
          )}
          {repos.length > 0 && (
            <ul className="divide-y divide-ink-800/70">
              {repos.map((r) => {
                const isConnected = connectedNames.has(r.full_name);
                const [owner, repo] = r.full_name.split("/");
                return (
                  <li key={r.full_name} className="flex items-center gap-3 px-6 py-3">
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
                    {isConnected ? (
                      <div className="flex items-center gap-2">
                        <span className="rounded-full bg-green-500/10 px-3 py-1 font-mono text-[11px] text-green-300">
                          connected
                        </span>
                        <button
                          onClick={() => {
                            const row = connected.find((c) => c.full_name === r.full_name);
                            if (row) disconnect(row.id);
                          }}
                          className="text-[11px] text-ink-500 transition-colors hover:text-red-300"
                        >
                          disconnect
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => connect(r.full_name)}
                        className="btn-ghost !px-3 !py-1 text-xs"
                      >
                        Connect
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}
