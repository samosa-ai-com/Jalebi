/**
 * Brew House — control shelf: backends, repos, screenings, findings,
 * throughput. Read-only instruments; every block links to its home page.
 */
import { Link } from "react-router-dom";
import type { BackendsResponse, Repo, Screen, ScreeningFinding, Task } from "../../types";

function Ring({ fraction, label }: { fraction: number; label: string }) {
  const r = 26;
  const c = 2 * Math.PI * r;
  const clamped = Math.max(0, Math.min(1, fraction));
  return (
    <svg viewBox="0 0 64 64" role="img" aria-label={label} className="h-16 w-16">
      <circle cx="32" cy="32" r={r} fill="none" stroke="#33271e" strokeWidth="7" />
      <circle
        cx="32"
        cy="32"
        r={r}
        fill="none"
        stroke="#ef9b2f"
        strokeWidth="7"
        strokeLinecap="round"
        strokeDasharray={`${clamped * c} ${c}`}
        transform="rotate(-90 32 32)"
      />
    </svg>
  );
}

const SEV_COLOR: Record<string, string> = {
  critical: "text-red-300",
  high: "text-syrup-300",
  medium: "text-chai-300",
  low: "text-ink-400",
};

export function ControlShelf({
  tasks,
  repos,
  screens,
  findings,
  backends,
  concurrency,
}: {
  tasks: Task[];
  repos: Repo[];
  screens: Screen[];
  findings: ScreeningFinding[];
  backends: BackendsResponse | null;
  concurrency: number;
}) {
  const active = tasks.filter((t) => t.status === "queued" || t.status === "running").length;
  const done = tasks.filter((t) => t.status === "done").length;
  const failed = tasks.filter(
    (t) => t.status === "failed" || t.status === "timed_out" || t.status === "interrupted"
  ).length;
  const needsYou = tasks.filter((t) => t.attention === "needs_you").length;
  const liveScreens = screens.filter(
    (s) => s.enabled && (s.latest_run?.status === "queued" || s.latest_run?.status === "running")
  );
  const webhookRepos = repos.filter((r) => r.webhook_registered).length;

  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      <section className="surface flex items-center gap-4 p-4" aria-label="Worker load">
        <Ring
          fraction={concurrency > 0 ? active / concurrency : 0}
          label={`worker load ${active} of ${concurrency}`}
        />
        <div>
          <h3 className="panel-title">Worker load</h3>
          <p className="mt-1 font-mono text-2xl tabular-nums text-ink-100">
            {active}
            <span className="text-sm text-ink-500"> / {concurrency} slots</span>
          </p>
          <p className="text-[11px] text-ink-500">
            {concurrency === 0
              ? "queue paused"
              : done > 0
                ? `${done} brewed to done`
                : "kettles warming up"}
          </p>
        </div>
      </section>

      <section className="surface p-4" aria-label="Configured backends">
        <div className="flex items-baseline justify-between">
          <h3 className="panel-title">Backends</h3>
          <Link to="/settings" className="link font-mono text-[11px]">
            settings →
          </Link>
        </div>
        {backends === null ? (
          <p className="mt-3 text-xs text-ink-600">Loading…</p>
        ) : backends.enabled.length === 0 ? (
          <p className="mt-3 text-xs text-ink-600">No backends enabled.</p>
        ) : (
          <ul className="mt-2 space-y-1.5">
            {backends.enabled.map((b) => (
              <li key={b} className="flex items-center gap-2 text-xs">
                <span className="h-1.5 w-1.5 rounded-full bg-syrup-400" />
                <span className="font-mono text-ink-200">{b}</span>
                {b === backends.default && (
                  <span className="rounded bg-syrup-500/10 px-1.5 py-0.5 font-mono text-[10px] text-syrup-300">
                    default
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
        {(failed > 0 || needsYou > 0) && (
          <p className="mt-2 font-mono text-[11px] tabular-nums">
            {[
              failed > 0 ? { text: `${failed} failed`, cls: "text-red-300" } : null,
              needsYou > 0 ? { text: `${needsYou} need you`, cls: "text-syrup-300" } : null,
            ]
              .filter((x) => x !== null)
              .map((x, i, arr) => (
                <span key={x.text} className={x.cls}>
                  {x.text}
                  {i < arr.length - 1 ? " · " : ""}
                </span>
              ))}
          </p>
        )}
      </section>

      <section className="surface p-4" aria-label="Repositories">
        <div className="flex items-baseline justify-between">
          <h3 className="panel-title">Repos</h3>
          <Link to="/repos" className="link font-mono text-[11px]">
            {repos.length} connected →
          </Link>
        </div>
        {repos.length === 0 ? (
          <p className="mt-3 text-xs text-ink-600">Nothing connected yet.</p>
        ) : (
          <>
            <div className="mt-2 flex flex-wrap gap-1.5" aria-hidden="true">
              {repos.slice(0, 14).map((r) => (
                <span
                  key={r.id}
                  title={r.full_name}
                  className={`h-2.5 w-2.5 rounded-full ${
                    r.webhook_registered ? "bg-syrup-400" : "bg-ink-700"
                  }`}
                />
              ))}
            </div>
            <p
              className="mt-2 truncate font-mono text-[11px] text-ink-500"
              title={repos.map((r) => r.full_name).join(", ")}
            >
              {repos
                .slice(0, 3)
                .map((r) => r.full_name.split("/")[1] ?? r.full_name)
                .join(" · ")}
              {repos.length > 3 ? ` +${repos.length - 3}` : ""}
            </p>
            <p className="text-[11px] text-ink-500">{webhookRepos} on webhook · rest polling</p>
          </>
        )}
      </section>

      <section className="surface p-4" aria-label="Screenings">
        <div className="flex items-baseline justify-between">
          <h3 className="panel-title">Screenings</h3>
          <Link to="/screenings" className="link font-mono text-[11px]">
            {screens.length} screens →
          </Link>
        </div>
        {liveScreens.length > 0 ? (
          <ul className="mt-2 space-y-1.5">
            {liveScreens.slice(0, 3).map((s) => (
              <li key={s.id} className="flex items-center gap-2 text-xs">
                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-syrup-400 brew-needs-you" />
                <Link
                  to="/screenings"
                  className="min-w-0 flex-1 truncate text-ink-200 hover:text-syrup-300"
                >
                  {s.name}
                </Link>
                <span className="font-mono text-[10px] text-ink-500">{s.latest_run?.status}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-3 text-xs text-ink-600">
            {screens.length === 0 ? "No screens configured." : "All quiet — no audit running."}
          </p>
        )}
        {findings.length > 0 && (
          <div className="brew-ticker mt-2 overflow-hidden" aria-label="Latest findings">
            <div className="brew-ticker-track flex w-max gap-4">
              {[...findings.slice(0, 6), ...findings.slice(0, 6)].map((f, i) => (
                <Link
                  key={`${f.screen_id}-${f.title}-${i}`}
                  to="/screenings"
                  className="whitespace-nowrap font-mono text-[10px]"
                  title={`${f.screen_name}: ${f.title}`}
                >
                  <span className={SEV_COLOR[f.severity] ?? "text-ink-400"}>[{f.severity}]</span>{" "}
                  <span className="text-ink-400">{f.title}</span>
                </Link>
              ))}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

export default ControlShelf;
