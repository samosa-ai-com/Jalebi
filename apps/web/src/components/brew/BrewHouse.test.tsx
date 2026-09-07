import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BrewHouse } from "./BrewHouse";

const AGENTS = [
  {
    id: "fixer",
    name: "Fixer",
    kind: "general",
    cli: "opencode",
    model: null,
    personality_md: "",
    skills: [],
    skill_ids: ["sec"],
    custom_instructions: "",
    enabled: true,
    description: "fixes things",
    avatar: null,
    created_at: "2026-09-01T00:00:00",
  },
];

const SKILLS = [
  {
    id: "sec",
    name: "Security",
    description: "d",
    content: "c",
    tags: [],
    created_at: "2026-09-01T00:00:00",
    updated_at: "2026-09-01T00:00:00",
  },
  {
    id: "docs",
    name: "Docs",
    description: "d",
    content: "c",
    tags: [],
    created_at: "2026-09-01T00:00:00",
    updated_at: "2026-09-01T00:00:00",
  },
];

const TASK = {
  id: 1,
  type: "freeform",
  repo_id: 1,
  repo_full_name: "owner/repo",
  source_branch: "main",
  target_branch: "main",
  agent_id: "fixer",
  cli: "opencode",
  model: null,
  pat_name: null,
  prompt: "brew the fix",
  status: "running",
  timeout_minutes: 30,
  retry_count: 0,
  pr_number: null,
  issues: [],
  prs: [],
  publish_mode: null,
  env_vars: [],
  created_at: "2026-09-07T00:00:00",
  updated_at: "2026-09-07T00:01:00",
  attention: "working",
  run: { id: 11, started_at: "2026-09-07T00:00:30", steps: [] },
  followups: [],
};

const REPOS = [
  {
    id: 1,
    full_name: "owner/repo",
    default_branch: "main",
    connected: true,
    webhook_registered: true,
    poll_fallback: false,
    check_runs_enabled: false,
    last_checked_at: null,
  },
];

const SCREENS = [
  {
    id: 1,
    repo_id: 1,
    name: "Nightly audit",
    system_prompt: "",
    cadence_cron: "0 2 * * *",
    scope_branch: null,
    cli: null,
    model: null,
    enabled: true,
    notify_ntfy: false,
    created_at: "2026-09-01T00:00:00",
    updated_at: "2026-09-01T00:00:00",
    latest_run: {
      id: 5,
      screening_id: 1,
      head_sha: null,
      status: "running",
      started_at: "2026-09-07T00:00:00",
      finished_at: null,
      finding_counts: {},
      finding_total: 0,
      error: null,
    },
  },
];

const FINDINGS = [
  {
    screen_id: 1,
    screen_name: "Nightly audit",
    repo_id: 1,
    repo_full_name: "owner/repo",
    run_id: 5,
    head_sha: null,
    finished_at: null,
    severity: "high",
    title: "Leaky token log",
    file: null,
    line: null,
    detail: null,
    recommendation: null,
  },
];

function stubFetch(handlers: Record<string, unknown>) {
  const fetchMock = vi.fn(async (url: string, _init?: RequestInit) => {
    const match = Object.entries(handlers).find(([needle]) => url.includes(needle));
    const value = match ? match[1] : [];
    return { ok: true, json: async () => value };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const HANDLERS = {
  "/api/screenings/findings": FINDINGS,
  "/api/screenings": SCREENS,
  "/api/agents": AGENTS,
  "/api/skills": SKILLS,
  "/api/backends": { backends: ["opencode", "codex"], enabled: ["opencode"], default: "opencode" },
  "/api/settings": { default_backend: "opencode", concurrency: 4 },
};

function renderHouse(tasks: unknown[] = [TASK]) {
  return render(
    <MemoryRouter>
      <BrewHouse tasks={tasks as never} repos={REPOS as never} onNewTask={() => {}} />
    </MemoryRouter>
  );
}

describe("BrewHouse", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("fries the active task on a kadhai station", async () => {
    stubFetch({ ...HANDLERS });
    renderHouse();
    expect(await screen.findByRole("button", { name: "Open task 1" })).toBeInTheDocument();
    expect(screen.getByText("#1")).toBeInTheDocument();
    expect(screen.getAllByText("Fixer").length).toBeGreaterThanOrEqual(1);
    // freeform fries a jalebi.
    expect(screen.getByText("jalebi")).toBeInTheDocument();
  });

  it("shows simmering stoves beside the frying one", async () => {
    stubFetch({ ...HANDLERS });
    renderHouse();
    await screen.findByRole("button", { name: "Open task 1" });
    // concurrency 4, one active → three simmering stoves.
    expect(screen.getByText("stove 2 · simmering")).toBeInTheDocument();
    expect(screen.getByText("stove 4 · simmering")).toBeInTheDocument();
  });

  it("opens the task when its station is clicked", async () => {
    stubFetch({ ...HANDLERS });
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route
            path="/"
            element={
              <BrewHouse tasks={[TASK] as never} repos={REPOS as never} onNewTask={() => {}} />
            }
          />
          <Route path="/tasks/:id" element={<div>detail page</div>} />
        </Routes>
      </MemoryRouter>
    );
    await userEvent.click(await screen.findByRole("button", { name: "Open task 1" }));
    expect(await screen.findByText("detail page")).toBeInTheDocument();
  });

  it("marks needs-you stations with a pulse ring", async () => {
    stubFetch({ ...HANDLERS });
    const { container } = renderHouse([{ ...TASK, attention: "needs_you" }]);
    await screen.findByRole("button", { name: "Open task 1" });
    expect(container.querySelector(".brew-needs-you")).not.toBeNull();
  });

  it("says when all kadhais are simmering with no orders", async () => {
    stubFetch({ ...HANDLERS });
    renderHouse([]);
    expect(await screen.findByText("stove 1 · simmering")).toBeInTheDocument();
    // The menu board invites the first order.
    expect(screen.getByText("Today's menu")).toBeInTheDocument();
  });

  it("bowls skills in the masala dabba and glows the ones seasoning now", async () => {
    stubFetch({ ...HANDLERS });
    const { container } = renderHouse();
    await screen.findByText("Masala dabba");
    expect(screen.getByText("sec")).toBeInTheDocument();
    expect(screen.getByText("docs")).toBeInTheDocument();
    // sec rides the Fixer agent frying task #1 → glowing bowl.
    expect(container.querySelector(".brew-glow")).not.toBeNull();
  });

  it("shelves agents with live task counts", async () => {
    stubFetch({ ...HANDLERS });
    renderHouse();
    expect(await screen.findAllByText("Fixer")).not.toHaveLength(0);
    expect(screen.getByText("● 1")).toBeInTheDocument();
  });

  it("lists enabled backends with the default badge", async () => {
    stubFetch({ ...HANDLERS });
    renderHouse();
    expect(await screen.findByText("opencode")).toBeInTheDocument();
    expect(screen.getByText("default")).toBeInTheDocument();
  });

  it("shows live screening runs and the findings ticker", async () => {
    stubFetch({ ...HANDLERS });
    renderHouse();
    expect(await screen.findByText("Nightly audit")).toBeInTheDocument();
    // The ticker loops its items for the marquee, so the title appears twice.
    expect(screen.getAllByText("Leaky token log").length).toBe(2);
  });

  it("gauges worker load from active tasks over slots", async () => {
    stubFetch({ ...HANDLERS });
    renderHouse();
    const ring = await screen.findByRole("img", { name: /worker load 1 of 4/ });
    expect(ring).toBeInTheDocument();
  });

  it("renders empty states without catalog data", async () => {
    stubFetch({
      "/api/screenings/findings": [],
      "/api/screenings": [],
      "/api/agents": [],
      "/api/skills": [],
      "/api/backends": { backends: [], enabled: [], default: "opencode" },
      "/api/settings": { default_backend: "opencode", concurrency: 4 },
    });
    renderHouse([]);
    expect(await screen.findByText("No skills in the library yet.")).toBeInTheDocument();
    expect(screen.getByText("No agents in the catalog yet.")).toBeInTheDocument();
    expect(screen.getByText("No screens configured.")).toBeInTheDocument();
  });

  it("announces a paused queue", async () => {
    stubFetch({ ...HANDLERS, "/api/settings": { default_backend: "opencode", concurrency: 0 } });
    renderHouse([]);
    expect(await screen.findAllByText(/queue paused/)).not.toHaveLength(0);
  });

  it("fries a samosa for issue_fix and pakoras for pr_review", async () => {
    stubFetch({ ...HANDLERS });
    renderHouse([
      { ...TASK, id: 2, type: "issue_fix", status: "queued", prompt: "fix it" },
      { ...TASK, id: 3, type: "pr_review", status: "queued", prompt: "review it" },
    ]);
    expect(await screen.findByRole("button", { name: "Open task 2" })).toBeInTheDocument();
    expect(screen.getByText("samosa")).toBeInTheDocument();
    expect(screen.getByText("pakora")).toBeInTheDocument();
  });

  it("strike-a-match calls back to start a new task", async () => {
    stubFetch({ ...HANDLERS });
    const onNewTask = vi.fn();
    render(
      <MemoryRouter>
        <BrewHouse tasks={[]} repos={REPOS as never} onNewTask={onNewTask} />
      </MemoryRouter>
    );
    await userEvent.click(await screen.findByRole("button", { name: /Strike a match on stove 1/ }));
    expect(onNewTask).toHaveBeenCalledTimes(1);
  });

  it("menu board totals the day and offers orders when idle", async () => {
    stubFetch({ ...HANDLERS });
    const doneToday = {
      ...TASK,
      id: 9,
      status: "done",
      type: "issue_fix",
      prompt: "fixed",
      updated_at: new Date().toISOString(),
    };
    renderHouse([TASK, doneToday]);
    expect(await screen.findByText("Today's menu")).toBeInTheDocument();
    // Engineering legend maps the snack to its task type with all-time counts.
    expect(screen.getByText("issue_fix")).toBeInTheDocument();
    expect(screen.getByText("freeform")).toBeInTheDocument();
    expect(screen.getByText("1 served")).toBeInTheDocument();
  });

  it("menu order buttons call back with their snack kind", async () => {
    stubFetch({ ...HANDLERS });
    const onNewTask = vi.fn();
    render(
      <MemoryRouter>
        <BrewHouse tasks={[]} repos={REPOS as never} onNewTask={onNewTask} />
      </MemoryRouter>
    );
    await userEvent.click(await screen.findByRole("button", { name: "New pr_review →" }));
    expect(onNewTask).toHaveBeenCalledTimes(1);
    expect(onNewTask).toHaveBeenCalledWith("pakora");
  });

  it("centers the kadhai strip while it fits", async () => {
    stubFetch({ ...HANDLERS });
    const { container } = renderHouse();
    await screen.findByRole("button", { name: "Open task 1" });
    const strip = container.querySelector(".mx-auto.w-max");
    expect(strip).not.toBeNull();
  });

  it("idle agents chatter on the resting shelf", async () => {
    stubFetch({ ...HANDLERS });
    renderHouse([]);
    await screen.findByText("Resting shelf");
    expect(await screen.findByRole("status")).toBeInTheDocument();
  });

  it("served ticker links recent dones to their tasks", async () => {
    stubFetch({ ...HANDLERS });
    renderHouse([{ ...TASK, id: 9, status: "done", prompt: "fixed" }]);
    const links = await screen.findAllByRole("link", { name: /#9/ });
    expect(links.length).toBeGreaterThanOrEqual(1);
    expect(links[0]).toHaveAttribute("href", "/tasks/9");
  });
});
