/**
 * Halwai Shop — masala dabba (skills as spice bowls) + resting shelf
 * (agents with idle chatter).
 *
 * The dabba is a round steel box; skills sit in bowls arranged in a ring
 * (8 visible + overflow count). A bowl glows and sheds falling spice
 * particles when its skill is inside a live task. Resting agents (zero
 * live tasks) take turns showing a speech bubble from the chatter pool
 * while the shop is idle — the shelf is never still.
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

const CHATTER_GENERAL = [
  "Use me — I'm getting cold",
  "I fry jalebis fast",
  "Got a bug? I'll squash it",
  "My kadhai is empty…",
  "Ghee is hot. I'm ready",
  "Bored. Feed me an issue",
  "Pick me, pick me!",
  "Fresh oil, steady flame",
];

const CHATTER_REVIEWER = [
  "Reviewer, reporting for duty",
  "Show me a PR to tear apart",
  "I read diffs for breakfast",
  "Line-by-line, no mercy",
];

const BOWL_COLORS = ["#f7b955", "#ef9b2f", "#d9b78c", "#c69b6b", "#ffd97a", "#b08050"];

function Dabba({ skills }: { skills: SkillChord[] }) {
  const shown = skills.slice(0, 8);
  const extra = skills.length - shown.length;
  const R = 44;
  return (
    <div className="flex items-center gap-3">
      <svg
        viewBox="0 0 120 120"
        role="img"
        aria-label="Masala dabba, spice box"
        className="h-24 w-24 shrink-0"
      >
        <circle cx="60" cy="60" r="56" fill="#2a2018" stroke="#b08050" strokeWidth="3" />
        <circle cx="60" cy="60" r="50" fill="none" stroke="#4a3a2d" strokeWidth="1" />
        <circle cx="60" cy="60" r="44" fill="none" stroke="#33271e" strokeWidth="1" />
        <path
          d="M18 44 A 48 48 0 0 1 60 12"
          fill="none"
          stroke="#f7b955"
          strokeWidth="2"
          opacity="0.5"
          strokeLinecap="round"
        />
        <circle cx="60" cy="60" r="13" fill="#1a1410" stroke="#b08050" strokeWidth="1.5" />
        <circle cx="60" cy="60" r="4" fill="none" stroke="#4a3a2d" />
        {shown.map((c, i) => {
          const a = (i / shown.length) * Math.PI * 2 - Math.PI / 2;
          const x = 60 + R * Math.cos(a);
          const y = 60 + R * Math.sin(a);
          const color = BOWL_COLORS[i % BOWL_COLORS.length];
          return (
            <g key={c.skill.id}>
              <circle cx={x} cy={y} r="14" fill="#4a3a2d" />
              <circle
                cx={x}
                cy={y}
                r="12.5"
                fill="#1a1410"
                stroke={c.inPlay ? "#ef9b2f" : "#221a14"}
                strokeWidth={c.inPlay ? 2.5 : 1.5}
                className={c.inPlay ? "brew-glow" : undefined}
              />
              <circle cx={x} cy={y + 2} r="8" fill={color} opacity={c.inPlay ? 0.95 : 0.45} />
              <ellipse cx={x - 2.5} cy={y - 1} rx="3" ry="1.8" fill="#f6efe5" opacity="0.35" />
              {c.inPlay && (
                <g fill={color}>
                  <circle cx={x - 3} cy={y - 8} r="1.4" className="brew-pinch" />
                  <circle
                    cx={x + 3}
                    cy={y - 10}
                    r="1.1"
                    className="brew-pinch"
                    style={{ animationDelay: "-0.7s" }}
                  />
                </g>
              )}
              <text
                x={x}
                y={y + 3.5}
                textAnchor="middle"
                fontSize="7"
                fill="#0d0a08"
                fontFamily="IBM Plex Mono, monospace"
                fontWeight="bold"
              >
                {c.skill.id.slice(0, 2).toUpperCase()}
              </text>
            </g>
          );
        })}
      </svg>
      <ul className="min-w-0 flex-1 space-y-1">
        {shown.map((c) => (
          <li key={c.skill.id}>
            <Link
              to="/skills"
              title={`${c.skill.name} — in ${c.uses} agent ${c.uses === 1 ? "kit" : "kits"}${c.inPlay ? " · seasoning now" : ""}`}
              className="flex items-baseline gap-2 text-[11px] transition-colors hover:text-syrup-300"
            >
              <span className={`font-mono ${c.inPlay ? "text-syrup-300" : "text-ink-400"}`}>
                {c.skill.id}
              </span>
              {c.inPlay && (
                <span className="font-mono text-[10px] text-syrup-400">✦ seasoning</span>
              )}
            </Link>
          </li>
        ))}
        {extra > 0 && (
          <li>
            <Link to="/skills" className="link font-mono text-[11px]">
              +{extra} more →
            </Link>
          </li>
        )}
        {shown.length === 0 && (
          <li className="text-xs text-ink-600">No skills in the library yet.</li>
        )}
      </ul>
    </div>
  );
}

export function MasalaDabba({
  skills,
  agents,
  idle,
  tick,
}: {
  skills: SkillChord[];
  agents: AgentChord[];
  idle: boolean;
  tick: number;
}) {
  // One resting agent talks at a time, rotating every ~5 s while idle.
  // Lines match the agent's personality (reviewers get review lines).
  // The bubble renders in-flow *below* its row, so it never covers the
  // agent name above and never clips at the top of the scroll box.
  const resting = agents.filter((a) => a.activeTasks === 0);
  const chatterIdx = resting.length > 0 ? Math.floor(tick / 5000) % resting.length : -1;
  const talkerId = idle && chatterIdx >= 0 ? resting[chatterIdx].agent.id : null;
  return (
    <div className="grid gap-4 lg:grid-cols-5">
      <section className="surface px-3 py-2.5 lg:col-span-3" aria-label="Masala dabba, spice box">
        <div className="flex items-baseline justify-between">
          <h3 className="panel-title">
            Masala dabba <span className="font-normal text-ink-500">(spice box)</span>
          </h3>
          <Link to="/skills" className="link font-mono text-[11px]">
            {skills.length} ingredients →
          </Link>
        </div>
        <div className="mt-2">
          <Dabba skills={skills} />
        </div>
        <p className="mt-2 text-[11px] text-ink-500">
          Bowls glow while their spice seasons a live fry.
        </p>
      </section>

      <section className="surface px-3 py-2.5 lg:col-span-2" aria-label="Agent resting shelf">
        <div className="flex items-baseline justify-between">
          <h3 className="panel-title">Resting shelf</h3>
          <Link to="/agents" className="link font-mono text-[11px]">
            {agents.length} agents →
          </Link>
        </div>
        {agents.length === 0 ? (
          <p className="mt-3 text-xs text-ink-600">No agents in the catalog yet.</p>
        ) : (
          <ul className="mt-1.5 max-h-32 space-y-1 overflow-y-auto">
            {agents.map(({ agent, activeTasks }) => {
              const talks = talkerId === agent.id;
              const pool = agent.kind === "reviewer" ? CHATTER_REVIEWER : CHATTER_GENERAL;
              const line = pool[Math.floor(tick / 5000) % pool.length];
              return (
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
                    <span className="min-w-0 flex-1 truncate text-xs text-ink-300">
                      {agent.name}
                    </span>
                    <span
                      className={`font-mono text-[10px] tabular-nums ${
                        activeTasks > 0 ? "text-syrup-300" : "text-ink-600"
                      }`}
                    >
                      {activeTasks > 0 ? `● ${activeTasks}` : "○ resting"}
                    </span>
                  </Link>
                  {talks && (
                    <span
                      role="status"
                      className="brew-chatter ml-7 line-clamp-2 rounded-lg rounded-tl-none border border-syrup-500/40 bg-ink-900 px-2 py-1 font-mono text-[10px] text-syrup-300"
                    >
                      <span className="text-ink-500">{agent.name}:</span> “{line}”
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}

export default MasalaDabba;
