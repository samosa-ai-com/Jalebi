import { Link } from "react-router-dom";
import type { LibrarySkill } from "../../types";

export interface ModuleSlot {
  skill: LibrarySkill;
  /** How many runners (agents) bundle this module */
  uses: number;
  /** True while an active worker is executing a task with an agent that includes this module */
  inPlay: boolean;
  /** 1-based core slot numbers */
  coreSlots?: number[];
}

const DOT_COLORS = [
  "bg-sky-400",
  "bg-emerald-400",
  "bg-amber-400",
  "bg-violet-400",
  "bg-chai-400",
  "bg-syrup-400",
];

function dotFor(id: string): string {
  let h = 0;
  for (let i = 0; i < id.length; i += 1) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return DOT_COLORS[h % DOT_COLORS.length];
}

export function ToolbeltRail({
  modules,
  hoveredCore = null,
  onHoverCore,
}: {
  modules: ModuleSlot[];
  hoveredCore?: number | null;
  onHoverCore?: (core: number | null) => void;
}) {
  return (
    <section
      className="surface flex min-h-0 flex-col overflow-hidden px-3 py-2.5 font-mono"
      aria-label="Toolbelt"
    >
      <div className="flex items-baseline justify-between">
        <h3 className="panel-title text-xs uppercase tracking-wider text-ink-300">
          Toolbelt (skills)
        </h3>
        <span className="text-[10px] text-ink-500">{modules.length} installed</span>
      </div>

      {modules.length === 0 ? (
        <p className="mt-3 text-xs text-ink-500">No skill modules installed.</p>
      ) : (
        <ul className="mt-2 min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-0.5">
          {modules.map(({ skill, uses, inPlay, coreSlots }) => {
            const isHighlighted =
              hoveredCore != null && coreSlots && coreSlots.includes(hoveredCore);

            return (
              <li
                key={skill.id}
                onMouseEnter={() => coreSlots?.[0] && onHoverCore?.(coreSlots[0])}
                onMouseLeave={() => onHoverCore?.(null)}
                className={`rounded-lg border transition-all duration-200 ${
                  isHighlighted
                    ? "border-sky-500 bg-sky-500/15 ring-1 ring-sky-400"
                    : inPlay
                      ? "ops-module-active border-sky-500/40 bg-sky-500/10 hover:border-sky-500/70"
                      : "border-ink-850 bg-ink-950/40 hover:border-ink-700"
                }`}
              >
                <Link
                  to="/skills"
                  state={{ from: "mission" }}
                  className="flex items-center gap-2 px-2 py-1.5 text-xs"
                >
                  <span className={`h-2 w-2 shrink-0 rounded-full ${dotFor(skill.id)}`} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-medium text-ink-200">
                      {skill.name}
                    </span>
                    <span className="block truncate text-[10px] text-ink-500">
                      {uses} runner{uses === 1 ? "" : "s"}
                    </span>
                  </span>

                  {inPlay && (
                    <span className="shrink-0 rounded-full bg-sky-500/15 px-2 py-0.5 text-[10px] font-semibold text-sky-300 border border-sky-500/30">
                      in play
                    </span>
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

export default ToolbeltRail;
