/**
 * Halwai Shop — mission-control view for the Tasks page.
 *
 * The shop floor sits center-stage: karhais (cooking pots) fry the active
 * tasks, queued orders wait as tickets above, finished dishes land on the
 * serving counter below. Cooks (agents) rest in the left rail with the
 * pantry (skills as ingredients); stats and ops instruments fill the right
 * rail. Everything drills into the existing pages; all data comes from
 * existing read-only GET endpoints (tasks + repos ride the Tasks page poll
 * via props).
 */
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import { avatarFor, avatarUrl } from "../../lib/agentAvatars";
import type {
  BackendsResponse,
  CatalogAgent,
  LibrarySkill,
  Repo,
  Screen,
  ScreeningFinding,
  SettingsMap,
  Task,
} from "../../types";
import "./brew.css";
import { ControlShelf } from "./ControlShelf";
import { CooksRail, type CookSlot } from "./CooksRail";
import { Pantry, type PantryIngredient } from "./Pantry";
import { ShopFloor } from "./ShopFloor";
import { StatsBoard } from "./StatsBoard";
import type { StationTask } from "./FryStation";
import { snackForType, type SnackKind } from "./snacks";

function parseTime(iso: string | null): number | null {
  if (!iso) return null;
  const zoned = /([zZ]|[+-]\d{2}:?\d{2})$/.test(iso);
  const t = new Date(zoned ? iso : `${iso}Z`).getTime();
  return Number.isNaN(t) ? null : t;
}

const SCREENS_POLL_MS = 15_000;

const ORDER_TYPES = ["freeform", "issue_fix", "pr_review"] as const;

export function BrewHouse({
  tasks,
  repos,
  onNewTask,
}: {
  tasks: Task[];
  repos: Repo[];
  onNewTask: (kind?: SnackKind) => void;
}) {
  const navigate = useNavigate();
  const [now, setNow] = useState(() => Date.now());
  const [agents, setAgents] = useState<CatalogAgent[]>([]);
  const [skills, setSkills] = useState<LibrarySkill[]>([]);
  const [backends, setBackends] = useState<BackendsResponse | null>(null);
  const [concurrency, setConcurrency] = useState(4);
  const [screens, setScreens] = useState<Screen[]>([]);
  const [findings, setFindings] = useState<ScreeningFinding[]>([]);

  // 1 s ticker for elapsed timers + the wall clock.
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  // Catalog data: fetched once (it barely changes under this view).
  useEffect(() => {
    let cancelled = false;
    api
      .getAgents(true)
      .then((a) => {
        if (!cancelled) setAgents(a ?? []);
      })
      .catch(() => {});
    api
      .getSkills()
      .then((s) => {
        if (!cancelled) setSkills(s ?? []);
      })
      .catch(() => {});
    api
      .getBackends()
      .then((b) => {
        if (!cancelled) setBackends(b);
      })
      .catch(() => {});
    api
      .getSettings()
      .then((s: SettingsMap) => {
        if (cancelled) return;
        if (typeof s.concurrency === "number" && s.concurrency >= 0) {
          setConcurrency(s.concurrency);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  // Screenings + recent findings: light poll, paused on unmount.
  useEffect(() => {
    let cancelled = false;
    const loadScreens = () => {
      api
        .getScreens()
        .then((s) => {
          if (!cancelled) setScreens(s ?? []);
        })
        .catch(() => {});
      api
        .getRecentFindings({ limit: 8 })
        .then((f) => {
          if (!cancelled) setFindings(f ?? []);
        })
        .catch(() => {});
    };
    loadScreens();
    const id = setInterval(loadScreens, SCREENS_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const agentById = useMemo(() => new Map(agents.map((a) => [a.id, a])), [agents]);
  const skillById = useMemo(() => new Map(skills.map((s) => [s.id, s])), [skills]);

  const stations = useMemo<StationTask[]>(() => {
    const active = tasks
      .filter((t) => t.status === "queued" || t.status === "running")
      .sort((a, b) => (a.status === b.status ? a.id - b.id : a.status === "running" ? -1 : 1));
    return active.map((task) => {
      const agent = task.agent_id ? agentById.get(task.agent_id) : undefined;
      return {
        task,
        agentName: agent?.name ?? null,
        avatarUrl: agent
          ? avatarUrl(
              avatarFor({
                id: agent.id,
                name: agent.name,
                description: agent.description,
                avatar: agent.avatar,
              })
            )
          : null,
        skillNames: (agent?.skill_ids ?? [])
          .map((id) => skillById.get(id)?.name)
          .filter((n): n is string => typeof n === "string"),
        startedAtMs: parseTime(task.run?.started_at ?? null),
        timeoutMs: task.timeout_minutes > 0 ? task.timeout_minutes * 60_000 : null,
        steps: task.run?.steps?.length ?? 0,
      };
    });
    // NOTE: no `now` dep — elapsed ticks live in FryStation so the
    // catalog derivations below stay state-driven.
  }, [tasks, agentById, skillById]);

  const slots = Math.max(concurrency, stations.length, 1);

  const ingredients = useMemo<PantryIngredient[]>(() => {
    const inPlay = new Set<string>();
    for (const s of stations) {
      const agent = s.task.agent_id ? agentById.get(s.task.agent_id) : undefined;
      for (const id of agent?.skill_ids ?? []) inPlay.add(id);
    }
    return skills
      .map((skill) => ({
        skill,
        uses: agents.filter((a) => a.skill_ids?.includes(skill.id) ?? false).length,
        inPlay: inPlay.has(skill.id),
      }))
      .sort((a, b) => Number(b.inPlay) - Number(a.inPlay) || b.uses - a.uses);
  }, [skills, agents, stations, agentById]);

  const cooks = useMemo<CookSlot[]>(() => {
    const stoveByAgent = new Map<string, number>();
    stations.forEach((s, i) => {
      if (s.task.agent_id && !stoveByAgent.has(s.task.agent_id)) {
        stoveByAgent.set(s.task.agent_id, i + 1);
      }
    });
    const counts = new Map<string, number>();
    for (const s of stations) {
      if (s.task.agent_id) counts.set(s.task.agent_id, (counts.get(s.task.agent_id) ?? 0) + 1);
    }
    return agents.map((agent) => ({
      agent,
      activeTasks: counts.get(agent.id) ?? 0,
      stoveSlot: stoveByAgent.get(agent.id) ?? null,
    }));
  }, [agents, stations]);

  const served = useMemo(() => {
    const done = tasks.filter((t) => t.status === "done");
    done.sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""));
    return done.slice(0, 3);
  }, [tasks]);

  const needsYou = tasks.filter((t) => t.attention === "needs_you").length;

  const orderCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const t of tasks) counts.set(t.type, (counts.get(t.type) ?? 0) + 1);
    return counts;
  }, [tasks]);

  const clock = new Date(now).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

  return (
    <div className="space-y-3 animate-fade-up">
      <div className="surface flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2">
        <img src="/samosa.png" alt="Samosa AI" className="h-6 w-6 object-contain" />
        <h2 className="panel-title">Halwai shop</h2>
        <p className="text-xs text-ink-500">
          {concurrency === 0
            ? "queue paused — burners banked"
            : stations.length === 0
              ? "all karhais simmering — no orders on the fire"
              : `${stations.length} order${stations.length === 1 ? "" : "s"} frying`}
          {needsYou > 0 && (
            <span className="text-syrup-300">
              {" · "}
              {needsYou} need{needsYou === 1 ? "s" : ""} you
            </span>
          )}
          {" · "}by Samosa AI
        </p>
        <span className="ml-auto font-mono text-xs tabular-nums text-ink-400">{clock}</span>
      </div>

      {/* Single-viewport mission grid on wide screens: side rails scroll
          internally, the page itself stays put. Stacks below xl. */}
      <div className="grid gap-3 xl:grid-cols-[230px_minmax(0,1fr)_300px] xl:overflow-hidden">
        <div className="flex min-h-0 flex-col gap-3 xl:max-h-[calc(100vh-16rem)] xl:min-h-[540px]">
          <div className="flex min-h-0 flex-1 flex-col [&>section]:flex-1">
            <CooksRail cooks={cooks} />
          </div>
          <div className="flex min-h-0 flex-1 flex-col [&>section]:flex-1">
            <Pantry ingredients={ingredients} />
          </div>
        </div>

        <div className="flex min-h-0 flex-col xl:max-h-[calc(100vh-16rem)] xl:min-h-[540px]">
          <ShopFloor
            stations={stations}
            slots={slots}
            served={served}
            now={now}
            onOpen={(id) => navigate(`/tasks/${id}`)}
            onOrder={() => onNewTask()}
          />
        </div>

        <div className="flex min-h-0 flex-col gap-3 xl:max-h-[calc(100vh-16rem)] xl:min-h-[540px] xl:overflow-y-auto xl:pr-0.5">
          <section className="surface px-3 py-2.5" aria-label="Today's menu">
            <h3 className="panel-title">Today&apos;s menu</h3>
            <ul className="mt-2 space-y-1.5">
              {ORDER_TYPES.map((type) => (
                <li
                  key={type}
                  className="flex items-center gap-2 rounded-lg border border-ink-800/60 px-2 py-1.5"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-mono text-xs text-ink-200">
                      {snackForType(type)}
                    </span>
                    <span className="block truncate font-mono text-[10px] text-ink-500">
                      {type} · {orderCounts.get(type) ?? 0} total
                    </span>
                  </span>
                  <button
                    type="button"
                    onClick={() => onNewTask(snackForType(type))}
                    className="shrink-0 cursor-pointer rounded-full border border-ink-700 px-2 py-0.5 font-mono text-[10px] text-syrup-300 transition-colors hover:border-syrup-500/60 hover:bg-syrup-500/10"
                  >
                    New {type} →
                  </button>
                </li>
              ))}
            </ul>
          </section>

          <StatsBoard tasks={tasks} now={now} />

          <ControlShelf
            tasks={tasks}
            repos={repos}
            screens={screens}
            findings={findings}
            backends={backends}
            concurrency={concurrency}
            compact
          />
        </div>
      </div>
    </div>
  );
}

export default BrewHouse;
