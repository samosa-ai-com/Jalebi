/**
 * Brew House — mission-control view for the Tasks page.
 *
 * A chai-brewery take on the agent queue: worker slots are kettle stations,
 * skills are jars on a spice rack, agents rest on a shelf, and a control
 * shelf carries backends / repos / screenings / throughput. Everything
 * drills into the existing pages; all data comes from existing read-only
 * GET endpoints (tasks + repos ride the Tasks page poll via props).
 */
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
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
import { FryStation, type StationTask } from "./FryStation";
import { MasalaDabba, type AgentChord, type SkillChord } from "./MasalaDabba";
import { SweetShelf } from "./SweetShelf";

function parseTime(iso: string | null): number | null {
  if (!iso) return null;
  const zoned = /([zZ]|[+-]\d{2}:?\d{2})$/.test(iso);
  const t = new Date(zoned ? iso : `${iso}Z`).getTime();
  return Number.isNaN(t) ? null : t;
}

const SCREENS_POLL_MS = 15_000;

export function BrewHouse({
  tasks,
  repos,
  onNewTask,
}: {
  tasks: Task[];
  repos: Repo[];
  onNewTask: () => void;
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
        startedAtMs: parseTime(task.run?.started_at ?? null),
        timeoutMs: task.timeout_minutes > 0 ? task.timeout_minutes * 60_000 : null,
        steps: task.run?.steps?.length ?? 0,
      };
    });
    // NOTE: no `now` dep — elapsed ticks live in KettleStation so the
    // catalog derivations below stay state-driven.
  }, [tasks, agentById]);

  const slots = Math.max(concurrency, stations.length, 1);

  const skillChords = useMemo<SkillChord[]>(() => {
    const inPlay = new Set<string>();
    for (const s of stations) {
      const agent = s.task.agent_id ? agentById.get(s.task.agent_id) : undefined;
      for (const id of agent?.skill_ids ?? []) inPlay.add(id);
    }
    return skills
      .map((skill) => ({
        skill,
        uses: agents.filter((a) => a.skill_ids.includes(skill.id)).length,
        inPlay: inPlay.has(skill.id),
      }))
      .sort((a, b) => Number(b.inPlay) - Number(a.inPlay) || b.uses - a.uses);
  }, [skills, agents, stations, agentById]);

  const agentChords = useMemo<AgentChord[]>(() => {
    const counts = new Map<string, number>();
    for (const s of stations) {
      if (s.task.agent_id) counts.set(s.task.agent_id, (counts.get(s.task.agent_id) ?? 0) + 1);
    }
    return agents.map((agent) => ({ agent, activeTasks: counts.get(agent.id) ?? 0 }));
  }, [agents, stations]);

  const clock = new Date(now).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

  return (
    <div className="space-y-4 animate-fade-up">
      <div className="surface flex flex-wrap items-center gap-x-4 gap-y-1 px-5 py-3">
        <svg viewBox="0 0 24 24" aria-hidden="true" className="h-6 w-6">
          <polygon points="12,3 3,21 21,21" fill="#f7b955" stroke="#7c2d12" strokeWidth="1.5" />
          <line x1="12" y1="3" x2="12" y2="21" stroke="#7c2d12" strokeWidth="1" opacity="0.6" />
        </svg>
        <h2 className="panel-title">Halwai shop</h2>
        <p className="text-xs text-ink-500">
          {concurrency === 0
            ? "queue paused — burners banked"
            : stations.length === 0
              ? "all kadhais simmering — no orders on the fire"
              : `${stations.length} order${stations.length === 1 ? "" : "s"} frying`}
          {" · "}by Samosa AI
        </p>
        <span className="ml-auto font-mono text-xs tabular-nums text-ink-400">{clock}</span>
      </div>

      <section className="surface p-4" aria-label="Kadhai stations">
        <div className="flex items-baseline justify-between">
          <h3 className="panel-title">Kadhais</h3>
          <Link
            to="/settings"
            className="font-mono text-[11px] text-ink-500"
            title="Worker slots come from the queue concurrency setting"
          >
            {slots} burner{slots === 1 ? "" : "s"} →
          </Link>
        </div>
        <div className="mt-2 flex gap-3 overflow-x-auto pb-1">
          {Array.from({ length: slots }, (_, i) => (
            <FryStation
              key={i}
              slot={i + 1}
              station={stations[i] ?? null}
              now={now}
              onOpen={(id) => navigate(`/tasks/${id}`)}
              onOrder={onNewTask}
            />
          ))}
        </div>
      </section>

      <MasalaDabba
        skills={skillChords}
        agents={agentChords}
        idle={stations.length === 0}
        tick={now}
      />

      <SweetShelf tasks={tasks} idle={stations.length === 0} onOrder={onNewTask} />

      <ControlShelf
        tasks={tasks}
        repos={repos}
        screens={screens}
        findings={findings}
        backends={backends}
        concurrency={concurrency}
      />
    </div>
  );
}

export default BrewHouse;
