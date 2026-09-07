/**
 * Brew House — spice rack (skills as jars) + resting shelf (agents).
 *
 * Jar fill shows how much of the rack each skill occupies by usage; jars
 * glow when one of their skills is in a currently brewing task. The agents
 * shelf shows every enabled agent with its live task count. All clicks
 * drill into the existing catalog pages.
 */
import { Link } from "react-router-dom";
import { avatarFor, avatarUrl } from "../../lib/agentAvatars";
import type { CatalogAgent, LibrarySkill } from "../../types";

export interface SkillChord {
  skill: LibrarySkill;
  uses: number;
  inPlay: boolean;
}

export interface AgentChord {
  agent: CatalogAgent;
  activeTasks: number;
}

function Jar({ chord }: { chord: SkillChord }) {
  const { skill, uses, inPlay } = chord;
  const level = Math.min(1, 0.15 + uses / Math.max(1, uses + 4));
  const bodyTop = 34;
  const bodyBottom = 78;
  const fillTop = bodyBottom - level * (bodyBottom - bodyTop);
  return (
    <Link
      to="/skills"
      title={`${skill.name} — in ${uses} agent ${uses === 1 ? "kit" : "kits"}${inPlay ? " · brewing now" : ""}`}
      aria-label={`Skill ${skill.name}${inPlay ? ", brewing now" : ""}`}
      className="group flex w-16 shrink-0 flex-col items-center"
    >
      <svg viewBox="0 0 48 88" role="img" aria-hidden="true" className="w-12">
        <rect x="12" y="4" width="24" height="8" rx="2" fill="#4a3a2d" />
        <rect
          x="8"
          y="14"
          width="32"
          height="68"
          rx="7"
          fill="#1a1410"
          stroke={inPlay ? "#ef9b2f" : "#33271e"}
          strokeWidth={inPlay ? 2 : 1.5}
          className={inPlay ? "brew-glow" : undefined}
        />
        <path
          d={`M11 ${fillTop} L37 ${fillTop} L37 75 Q37 79 33 79 L15 79 Q11 79 11 75 Z`}
          fill={inPlay ? "#ef9b2f" : "#b08050"}
          opacity={inPlay ? 0.75 : 0.4}
        />
        {inPlay && (
          <circle cx="20" cy={fillTop + 12} r="2" fill="#ffd97a" className="brew-bubble" />
        )}
        {inPlay && (
          <circle
            cx="28"
            cy={fillTop + 20}
            r="1.5"
            fill="#ffd97a"
            className="brew-bubble"
            style={{ animationDelay: "-0.8s" }}
          />
        )}
      </svg>
      <span className="mt-1 w-full truncate text-center font-mono text-[10px] text-ink-400 group-hover:text-syrup-300">
        {skill.id}
      </span>
    </Link>
  );
}

export function SpiceRack({ skills, agents }: { skills: SkillChord[]; agents: AgentChord[] }) {
  const visible = skills.slice(0, 12);
  return (
    <div className="grid gap-4 lg:grid-cols-5">
      <section className="surface p-4 lg:col-span-3" aria-label="Skill spice rack">
        <div className="flex items-baseline justify-between">
          <h3 className="panel-title">Spice rack</h3>
          <Link to="/skills" className="link font-mono text-[11px]">
            {skills.length} skills →
          </Link>
        </div>
        {visible.length === 0 ? (
          <p className="mt-3 text-xs text-ink-600">No skills in the library yet.</p>
        ) : (
          <div className="mt-2 flex gap-1 overflow-x-auto pb-1">
            {visible.map((c) => (
              <Jar key={c.skill.id} chord={c} />
            ))}
          </div>
        )}
        <p className="mt-2 text-[11px] text-ink-500">
          Fill = how many agent kits carry it · glowing jars are brewing in a live task right now.
        </p>
      </section>

      <section className="surface p-4 lg:col-span-2" aria-label="Agent resting shelf">
        <div className="flex items-baseline justify-between">
          <h3 className="panel-title">Resting shelf</h3>
          <Link to="/agents" className="link font-mono text-[11px]">
            {agents.length} agents →
          </Link>
        </div>
        {agents.length === 0 ? (
          <p className="mt-3 text-xs text-ink-600">No agents in the catalog yet.</p>
        ) : (
          <ul className="mt-2 max-h-44 space-y-1.5 overflow-y-auto">
            {agents.map(({ agent, activeTasks }) => (
              <li key={agent.id}>
                <Link
                  to="/agents"
                  className="flex items-center gap-2 rounded-lg px-2 py-1 transition-colors hover:bg-ink-875/60"
                  title={`${agent.name} — ${activeTasks} live ${activeTasks === 1 ? "task" : "tasks"}`}
                >
                  <img
                    src={avatarUrl(
                      avatarFor({
                        id: agent.id,
                        name: agent.name,
                        description: agent.description,
                        avatar: agent.avatar,
                      })
                    )}
                    alt=""
                    className="h-5 w-5 rounded-full"
                  />
                  <span className="min-w-0 flex-1 truncate text-xs text-ink-300">{agent.name}</span>
                  <span
                    className={`font-mono text-[10px] tabular-nums ${
                      activeTasks > 0 ? "text-syrup-300" : "text-ink-600"
                    }`}
                  >
                    {activeTasks > 0 ? `● ${activeTasks}` : "○ resting"}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

export default SpiceRack;
