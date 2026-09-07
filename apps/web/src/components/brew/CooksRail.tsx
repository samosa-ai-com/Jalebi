/**
 * Halwai Shop — cooks rail. Agents are the shop's cooks: resting in the
 * rail when idle, pinned to a karhai (cooking pot) while they fry a task.
 * Plain cards, no art — the geometric avatar carries the identity.
 */
import type { CatalogAgent } from "../../types";
import { avatarFor, avatarUrl } from "../../lib/agentAvatars";

export interface CookSlot {
  agent: CatalogAgent;
  activeTasks: number;
  /** 1-based stove number the cook is frying at, or null when resting. */
  stoveSlot: number | null;
}

export function CooksRail({ cooks }: { cooks: CookSlot[] }) {
  return (
    <section className="surface flex min-h-0 flex-col px-3 py-2.5" aria-label="Cooks">
      <h3 className="panel-title">Cooks</h3>
      <p className="mt-0.5 text-[11px] text-ink-500">{cooks.length} in the kitchen</p>
      {cooks.length === 0 ? (
        <p className="mt-3 text-xs text-ink-600">No agents in the catalog yet.</p>
      ) : (
        <ul className="mt-2 min-h-0 space-y-1.5 overflow-y-auto pr-0.5">
          {cooks.map(({ agent, activeTasks, stoveSlot }) => {
            const busy = activeTasks > 0;
            return (
              <li
                key={agent.id}
                className={`flex items-center gap-2 rounded-lg border px-2 py-1.5 ${
                  busy ? "border-syrup-500/40 bg-syrup-500/5" : "border-ink-800/60"
                }`}
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
                  className="h-6 w-6 shrink-0 rounded-full"
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs text-ink-200">{agent.name}</span>
                  <span className="block truncate font-mono text-[10px] text-ink-500">
                    {agent.cli}
                    {agent.model ? ` · ${agent.model}` : ""}
                  </span>
                </span>
                {busy ? (
                  <span className="shrink-0 rounded-full bg-syrup-500/15 px-1.5 py-0.5 font-mono text-[10px] text-syrup-300">
                    karhai {stoveSlot ?? "·"}
                  </span>
                ) : (
                  <span className="shrink-0 font-mono text-[10px] text-ink-600">resting</span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

export default CooksRail;
