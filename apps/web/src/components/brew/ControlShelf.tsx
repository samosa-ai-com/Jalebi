/**
 * Brew House — control shelf: backends, repos, screenings, findings,
 * throughput. Read-only instruments; every block links to its home page.
 */
import { Link } from "react-router-dom";
import type { BackendsResponse, Repo, Screen, ScreeningFinding, Task } from "../../types";
import { qualifiedScreenName } from "../../lib/screeningPrompt";
import { SEV_COLOR } from "./snacks";

function Ring({ fraction, label }: { fraction: number; label: string }) {
  const r = 26;
  const c = 2 * Math.PI * r;
  const clamped = Math.max(0, Math.min(1, fraction));
  return (
    <svg viewBox="0 0 64 64" role="img" aria-label={label} className="h-12 w-12">
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

/** Plain-language status for recent-task links, so the state is not color-only. */
const STATUS_LABEL: Record<string, string> = {
  queued: "queued",
  running: "running",
  done: "done",
  failed: "failed",
  timed_out: "timed out",
  interrupted: "interrupted",
  cancelled: "cancelled",
  needs_approval: "needs approval",
  blocked: "blocked",
};

function statusDot(status: string): string {
  switch (status) {
    case "running":
      return "bg-syrup-400 animate-pulse-dot";
    case "queued":
      return "bg-ink-400";
    case "done":
      return "bg-green-400";
    case "failed":
    case "timed_out":
    case "interrupted":
      return "bg-red-400";
    case "needs_approval":
    case "waiting_review":
      return "bg-purple-400";
    case "blocked":
      return "bg-orange-400";
    default:
      return "bg-ink-500";
  }
}

export function ControlShelf({
  tasks,
  repos,
  screens,
  findings,
  backends,
  concurrency,
  compact = false,
}: {
  tasks: Task[];
  repos: Repo[];
  screens: Screen[];
  findings: ScreeningFinding[];
  backends: BackendsResponse | null;
  concurrency: number;
  /** Stacked rail mode for the mission-control side column. */
  compact?: boolean;
}) {
  const active = tasks.filter((t) => t.status === "queued" || t.status === "running").length;
  const done = tasks.filter((t) => t.status === "done").length;
  const liveScreens = screens.filter(
    (s) => s.enabled && (s.latest_run?.status === "queued" || s.latest_run?.status === "running")
  );
  const webhookRepos = repos.filter((r) => r.webhook_registered).length;
  const recentTasks = [...tasks]
    .sort((a, b) => {
      const ta = new Date(a.updated_at).getTime() || 0;
      const tb = new Date(b.updated_at).getTime() || 0;
      return tb - ta;
    })
    .slice(0, 3);

  return (
    <div
      className={
        compact
          ? "grid min-h-0 min-w-0 shrink-0 grid-cols-2 gap-2 xl:h-full xl:grid-rows-2"
          : "grid min-w-0 gap-4 md:grid-cols-2 xl:grid-cols-4"
      }
    >
      <section
        className={`surface flex ${compact ? "min-h-0 min-w-0 flex-col items-start gap-1 overflow-y-auto px-2.5 py-2" : "items-center gap-4 p-4"}`}
        aria-label="Worker load"
      >
        <Ring
          fraction={concurrency > 0 ? active / concurrency : 0}
          label={`worker load ${active} of ${concurrency}`}
        />
        <div>
          <h3 className="panel-title">Worker load</h3>
          <p
            className={`mt-1 font-mono tabular-nums text-ink-100 ${compact ? "text-xl" : "text-2xl"}`}
          >
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

      <section
        className={`surface ${compact ? "flex min-h-0 min-w-0 flex-col overflow-y-auto px-2.5 py-2" : "px-3 py-2.5"}`}
        aria-label="Configured backends"
      >
        <div className="flex items-baseline justify-between gap-1">
          <h3 className="panel-title truncate">Backends</h3>
          <Link
            to="/settings?section=agent"
            state={{ from: "mission" }}
            title="Backend settings"
            aria-label="Backend settings"
            className="link shrink-0 whitespace-nowrap font-mono text-[11px]"
          >
            {compact ? "→" : "settings →"}
          </Link>
        </div>
        {backends === null ? (
          <p className="mt-3 text-xs text-ink-500">Loading…</p>
        ) : (backends.enabled ?? []).length === 0 ? (
          <p className="mt-3 text-xs text-ink-500">No backends enabled.</p>
        ) : (
          <ul
            className={`mt-2 space-y-1.5 ${
              compact ? "min-h-0 flex-1 overflow-y-auto pr-0.5" : ""
            }`}
          >
            {(backends.enabled ?? []).map((b) => (
              <li
                key={b}
                className={compact ? "min-w-0 text-xs" : "flex items-center gap-2 text-xs"}
              >
                <span className={compact ? "flex items-center gap-2" : "contents"}>
                  <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-syrup-400" />
                  <span className="truncate font-mono text-ink-200">{b}</span>
                </span>
                {b === backends.default && (
                  <span className="mt-0.5 inline-block rounded bg-syrup-500/10 px-1.5 py-0.5 font-mono text-[10px] text-syrup-300">
                    default
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section
        className={`surface ${compact ? "flex min-h-0 min-w-0 flex-col overflow-y-auto px-2.5 py-2" : "px-3 py-2.5"}`}
        aria-label="Repositories"
      >
        <div className="flex items-baseline justify-between gap-1">
          <h3 className="panel-title truncate">Repos</h3>
          <Link
            to="/repos"
            state={{ from: "mission" }}
            title={`${repos.length} connected — open repos`}
            aria-label={`${repos.length} connected — open repos`}
            className="link shrink-0 whitespace-nowrap font-mono text-[11px]"
          >
            {compact ? `${repos.length} →` : `${repos.length} connected →`}
          </Link>
        </div>
        {repos.length === 0 ? (
          <p className="mt-3 text-xs text-ink-500">No repos connected yet.</p>
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

      {compact ? (
        <section
          className="surface flex min-h-0 min-w-0 flex-col overflow-y-auto px-2.5 py-2"
          aria-label="Recent"
        >
          <div className="flex items-baseline justify-between gap-1">
            <h3 className="panel-title truncate">
              <Link to="/?view=queue" className="hover:text-syrup-300">
                Recent
              </Link>
            </h3>
            <Link
              to="/?view=queue"
              title="Recent tasks"
              aria-label="Recent tasks"
              className="link shrink-0 whitespace-nowrap font-mono text-[11px]"
            >
              →
            </Link>
          </div>
          {recentTasks.length === 0 ? (
            <p className="mt-3 text-xs text-ink-500">No tasks yet.</p>
          ) : (
            <ul className="mt-2 min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-0.5">
              {recentTasks.map((t) => (
                <li key={t.id} className="min-w-0 text-xs">
                  <Link
                    to={`/tasks/${t.id}`}
                    state={{ from: "mission" }}
                    aria-label={`Task #${t.id}, ${t.type}, ${STATUS_LABEL[t.status] ?? t.status}`}
                    className="flex items-center gap-2 text-ink-200 hover:text-syrup-300"
                  >
                    <span
                      aria-hidden="true"
                      className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusDot(t.status)}`}
                    />
                    <span className="font-mono">#{t.id}</span>
                    <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-ink-500 text-right">
                      {t.type}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : (
        <section
          className="surface px-3 py-2.5"
          aria-label="Screenings"
        >
          <div className="flex items-baseline justify-between gap-1">
            <h3 className="panel-title truncate">Screenings</h3>
            <Link
              to="/screenings"
              state={{ from: "mission" }}
              title={`${screens.length} screens — open screenings`}
              aria-label={`${screens.length} screens — open screenings`}
              className="link shrink-0 whitespace-nowrap font-mono text-[11px]"
            >
              {`${screens.length} screens →`}
            </Link>
          </div>
          {liveScreens.length > 0 ? (
            <ul className="mt-2 space-y-1.5">
              {liveScreens.slice(0, 3).map((s) => (
                <li key={s.id} className="flex items-center gap-2 text-xs">
                  <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-syrup-400 brew-needs-you" />
                  <Link
                    to="/screenings"
                    state={{ from: "mission" }}
                    title={qualifiedScreenName(
                      s.name,
                      repos.find((r) => r.id === s.repo_id)?.full_name
                    )}
                    className="min-w-0 flex-1 truncate text-ink-200 hover:text-syrup-300"
                  >
                    {qualifiedScreenName(s.name, repos.find((r) => r.id === s.repo_id)?.full_name)}
                  </Link>
                  <span className="font-mono text-[10px] text-ink-500">{s.latest_run?.status}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-3 text-xs text-ink-500">
              {screens.length === 0 ? "No screens configured." : "All quiet — no audit running."}
            </p>
          )}
          {findings.length > 0 && (
            <div
              className="brew-ticker mt-2 overflow-hidden"
              role="group"
              aria-label="Latest findings"
            >
              <div className="brew-ticker-track flex w-max gap-4">
                {[...findings.slice(0, 6), ...findings.slice(0, 6)].map((f, i) =>
                  // The second copy only feeds the seamless marquee loop —
                  // hide it from keyboards and screen readers.
                  i < Math.min(6, findings.length) ? (
                    <Link
                      key={`${f.screen_id}-${f.title}-${i}`}
                      to="/screenings"
                      state={{ from: "mission" }}
                      className="whitespace-nowrap font-mono text-[10px]"
                      title={`${qualifiedScreenName(f.screen_name, f.repo_full_name)}: ${f.title}`}
                    >
                      <span className={SEV_COLOR[f.severity] ?? "text-ink-400"}>[{f.severity}]</span>{" "}
                      <span className="text-ink-400">{f.title}</span>
                    </Link>
                  ) : (
                    <span
                      key={`${f.screen_id}-${f.title}-${i}`}
                      aria-hidden="true"
                      className="whitespace-nowrap font-mono text-[10px]"
                    >
                      <span className={SEV_COLOR[f.severity] ?? "text-ink-400"}>[{f.severity}]</span>{" "}
                      <span className="text-ink-400">{f.title}</span>
                    </span>
                  )
                )}
              </div>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

export default ControlShelf;
