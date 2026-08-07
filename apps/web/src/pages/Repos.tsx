import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { Repo } from "../types";

export default function Repos() {
  const [repos, setRepos] = useState<Repo[]>([]);
  const [fullName, setFullName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .getRepos()
      .then(setRepos)
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!fullName.trim() || !fullName.includes("/")) return;
    setBusy(true);
    setError(null);
    try {
      await api.connectRepo(fullName.trim());
      setFullName("");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to connect repo");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <header className="animate-fade-up">
        <h1 className="text-3xl font-bold tracking-tight text-ink-100">Repos</h1>
        <p className="mt-1 text-sm text-ink-500">
          The connected repositories the agent can open worktrees in.
        </p>
      </header>

      <form
        onSubmit={submit}
        className="surface flex flex-col gap-3 p-6 sm:flex-row sm:items-end animate-fade-up"
        style={{ animationDelay: "0.05s" }}
      >
        <label className="flex-1">
          <span className="mb-1.5 block text-xs font-medium text-ink-400">
            Owner / repository
          </span>
          <input
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            placeholder="owner/repo"
            className="field font-mono"
            spellCheck={false}
          />
        </label>
        <button
          type="submit"
          disabled={busy || !fullName.includes("/")}
          className="btn-primary"
        >
          {busy ? "Connecting…" : "Connect"}
        </button>
      </form>

      {error && <p className="text-sm text-red-400">{error}</p>}

      <section className="surface animate-fade-up" style={{ animationDelay: "0.1s" }}>
        <div className="border-b border-ink-800 px-6 py-4">
          <h2 className="panel-title">Connected · {repos.length}</h2>
        </div>
        {repos.length === 0 && (
          <div className="px-6 py-10 text-center text-sm text-ink-600">
            Nothing connected yet. Type an <code className="font-mono">owner/repo</code> above, or
            pick one from the GitHub page.
          </div>
        )}
        <ul className="divide-y divide-ink-800/70">
          {repos.map((r) => {
            const [owner, repo] = r.full_name.split("/");
            return (
              <li key={r.id} className="flex items-center gap-3 px-6 py-3.5">
                <span className="h-2 w-2 shrink-0 rounded-full bg-syrup-400" />
                <span className="min-w-0 flex-1 font-mono text-sm text-ink-200">
                  <span className="text-ink-500">{owner}/</span>
                  {repo}
                </span>
                <span className="rounded bg-ink-850 px-2 py-0.5 font-mono text-[11px] text-ink-400">
                  {r.default_branch}
                </span>
                <span className="hidden font-mono text-[11px] text-ink-600 sm:block">
                  #{r.id}
                </span>
                <button
                  onClick={async () => {
                    try {
                      await api.disconnectRepo(r.id);
                      load();
                    } catch (e) {
                      setError(e instanceof Error ? e.message : "failed to disconnect");
                    }
                  }}
                  className="text-[11px] text-ink-500 transition-colors hover:text-red-300"
                >
                  disconnect
                </button>
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}
