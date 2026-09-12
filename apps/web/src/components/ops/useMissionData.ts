import { useEffect, useMemo, useState } from "react";
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
import { jobForType, type JobKind } from "./jobStyle";

export interface WorkerSlot {
  task: Task;
  agentName: string | null;
  avatarUrl: string | null;
  skillNames: string[];
  startedAtMs: number | null;
  timeoutMs: number | null;
  steps: number;
  repoName: string | null;
}

export interface PendingJob {
  task: Task;
}

export interface RunnerSlot {
  agent: CatalogAgent;
  activeTasks: number;
  coreIndex: number | null;
}

export interface ModuleSlot {
  skill: LibrarySkill;
  uses: number;
  inPlay: boolean;
  coreSlots: number[];
}

export interface OrderCountRow {
  type: string;
  total: number;
  kind: JobKind;
}

function parseTime(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const zoned = /([zZ]|[+-]\d{2}:?\d{2})$/.test(iso);
  const t = new Date(zoned ? iso : `${iso}Z`).getTime();
  return Number.isNaN(t) ? null : t;
}

const SCREENS_POLL_MS = 15_000;
const ORDER_TYPES = ["freeform", "issue_fix", "pr_review"] as const;

export function useMissionData({ tasks, repos }: { tasks: Task[]; repos: Repo[] }) {
  const [now, setNow] = useState(() => Date.now());
  const [agents, setAgents] = useState<CatalogAgent[]>([]);
  const [skills, setSkills] = useState<LibrarySkill[]>([]);
  const [backends, setBackends] = useState<BackendsResponse | null>(null);
  const [concurrency, setConcurrency] = useState(4);
  const [screens, setScreens] = useState<Screen[]>([]);
  const [findings, setFindings] = useState<ScreeningFinding[]>([]);

  // 1s ticker for elapsed timers & live wall clock
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  // Catalog data: fetched once on mount
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

  // Screenings + recent findings: polled every 15 seconds
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
  const repoById = useMemo(() => new Map(repos.map((r) => [r.id, r.full_name])), [repos]);

  const workerSlots = useMemo<WorkerSlot[]>(() => {
    const active = tasks
      .filter((t) => t.status === "running")
      .sort((a, b) => a.id - b.id);

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
        repoName: task.repo_full_name ?? repoById.get(task.repo_id) ?? null,
      };
    });
  }, [tasks, agentById, skillById, repoById]);

  const pending = useMemo<PendingJob[]>(() => {
    const list = tasks
      .filter((t) => t.status === "queued" || t.status === "blocked")
      .sort((a, b) => {
        if (a.status === b.status) return a.id - b.id;
        return a.status === "queued" ? -1 : 1;
      });
    return list.map((task) => ({ task }));
  }, [tasks]);

  const slots = Math.max(concurrency, workerSlots.length, 1);

  const modules = useMemo<ModuleSlot[]>(() => {
    const slotsBySkill = new Map<string, number[]>();
    workerSlots.forEach((s, idx) => {
      const agent = s.task.agent_id ? agentById.get(s.task.agent_id) : undefined;
      for (const id of agent?.skill_ids ?? []) {
        const list = slotsBySkill.get(id) ?? [];
        list.push(idx + 1);
        slotsBySkill.set(id, list);
      }
    });

    return skills
      .map((skill) => ({
        skill,
        uses: agents.filter((a) => a.skill_ids?.includes(skill.id) ?? false).length,
        inPlay: slotsBySkill.has(skill.id),
        coreSlots: slotsBySkill.get(skill.id) ?? [],
      }))
      .sort((a, b) => Number(b.inPlay) - Number(a.inPlay) || b.uses - a.uses);
  }, [skills, agents, workerSlots, agentById]);

  const runners = useMemo<RunnerSlot[]>(() => {
    const coreByAgent = new Map<string, number>();
    workerSlots.forEach((s, i) => {
      if (s.task.agent_id && !coreByAgent.has(s.task.agent_id)) {
        coreByAgent.set(s.task.agent_id, i + 1);
      }
    });
    const counts = new Map<string, number>();
    for (const s of workerSlots) {
      if (s.task.agent_id) counts.set(s.task.agent_id, (counts.get(s.task.agent_id) ?? 0) + 1);
    }
    return agents.map((agent) => ({
      agent,
      activeTasks: counts.get(agent.id) ?? 0,
      coreIndex: coreByAgent.get(agent.id) ?? null,
    }));
  }, [agents, workerSlots]);

  const shipped = useMemo(() => {
    const done = tasks.filter((t) => t.status === "done");
    done.sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""));
    return done.slice(0, 3);
  }, [tasks]);

  const faulted = useMemo(() => {
    const failed = tasks.filter(
      (t) => t.status === "failed" || t.status === "timed_out" || t.status === "interrupted"
    );
    failed.sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""));
    return failed.slice(0, 3);
  }, [tasks]);

  // Count every task needing attention, not just running ones — a task
  // awaiting approval is not "running" but still needs the operator.
  const needsYou = useMemo(
    () => tasks.filter((t) => t.attention === "needs_you").length,
    [tasks]
  );

  const orderCounts = useMemo<OrderCountRow[]>(() => {
    const counts = new Map<string, number>();
    for (const t of tasks) counts.set(t.type, (counts.get(t.type) ?? 0) + 1);
    return ORDER_TYPES.map((type) => ({
      type,
      total: counts.get(type) ?? 0,
      kind: jobForType(type),
    }));
  }, [tasks]);

  return {
    now,
    agents,
    skills,
    backends,
    concurrency,
    screens,
    findings,
    workerSlots,
    slots,
    modules,
    runners,
    shipped,
    faulted,
    needsYou,
    orderCounts,
    pending,
  };
}
