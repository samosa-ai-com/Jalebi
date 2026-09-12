import { Link } from "react-router-dom";
import { avatarFor, avatarUrl } from "../../lib/agentAvatars";
import type { CatalogAgent } from "../../types";

export interface RunnerSlot {
  agent: CatalogAgent;
  activeTasks: number;
  /** 1-based core number the runner is executing on, or null when idle. */
  coreIndex: number | null;
}

export function RunnersRail({
  runners,
  hoveredCore = null,
  onHoverCore,
}: {
  runners: RunnerSlot[];
  hoveredCore?: number | null;
  onHoverCore?: (core: number | null) => void;
}) {
  return (
    <section
      className="surface flex min-h-0 flex-col overflow-hidden px-3 py-2.5 font-mono"
      aria-label="Runners"
    >
      <div className="flex items-baseline justify-between">
        <h3 className="panel-title text-xs uppercase tracking-wider text-ink-300">
          Runners (agents)
        </h3>
        <span className="text-[10px] text-ink-500">{runners.length} registered</span>
      </div>

      {runners.length === 0 ? (
        <p className="mt-3 text-xs text-ink-500">No agents registered in catalog.</p>
      ) : (
        <ul className="mt-2 min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-0.5">
          {runners.map(({ agent, activeTasks, coreIndex }) => {
            const isBusy = activeTasks > 0;
            const isHighlighted = coreIndex != null && hoveredCore === coreIndex;

            return (
              <li
                key={agent.id}
                onMouseEnter={() => coreIndex && onHoverCore?.(coreIndex)}
                onMouseLeave={() => onHoverCore?.(null)}
                className={`rounded-lg border transition-all duration-200 ${
                  isHighlighted
                    ? "border-sky-500 bg-sky-500/15 ring-1 ring-sky-400"
                    : isBusy
                      ? "ops-runner-working border-sky-500/40 bg-sky-500/5 hover:border-sky-500/70"
                      : "ops-runner-bob border-ink-850 bg-ink-950/40 hover:border-ink-700"
                }`}
              >
                <Link
                  to="/agents"
                  state={{ from: "mission" }}
                  className="flex items-center gap-2 px-2 py-1.5 text-xs"
                >
                  <img
                    src={
                      avatarUrl(
                        avatarFor({
                          id: agent.id,
                          name: agent.name,
                          description: agent.description,
                          avatar: agent.avatar,
                        })
                      ) ?? ""
                    }
                    alt=""
                    className="h-6 w-6 shrink-0 rounded-full bg-ink-800"
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-medium text-ink-200">
                      {agent.name}
                    </span>
                    <span className="block truncate text-[10px] text-ink-500">
                      {agent.cli}
                      {agent.model ? ` · ${agent.model}` : ""}
                    </span>
                  </span>

                  {isBusy ? (
                    <span className="shrink-0 rounded-full bg-sky-500/15 px-2 py-0.5 text-[10px] font-semibold text-sky-300 border border-sky-500/30">
                      core {coreIndex ?? "·"}
                    </span>
                  ) : (
                    <span className="shrink-0 text-[10px] text-ink-500">idle</span>
                  )}
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

export default RunnersRail;
