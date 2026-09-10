import { render, screen, within } from "@testing-library/react";
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
  check_run_id: null,
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

  it("fries the active task on a karhai station", async () => {
    stubFetch({ ...HANDLERS });
    renderHouse();
    expect(await screen.findByRole("button", { name: /Open task 1/ })).toBeInTheDocument();
    expect(screen.getByText("#1")).toBeInTheDocument();
    expect(screen.getAllByText("Fixer").length).toBeGreaterThanOrEqual(1);
    // freeform fries a jalebi (stove card + menu row).
    expect(screen.getAllByText("jalebi").length).toBeGreaterThanOrEqual(2);
    // The floor names the pot for everyone.
    expect(screen.getByRole("region", { name: "Karhais (running tasks)" })).toBeInTheDocument();
  });

  it("bounds a long backends list so it scrolls inside its card", async () => {
    const many = Array.from({ length: 30 }, (_, i) => `backend-${i}`);
    stubFetch({
      ...HANDLERS,
      "/api/backends": { backends: many, enabled: many, default: many[0] },
    });
    renderHouse();
    const card = await screen.findByRole("region", { name: "Configured backends" });
    const list = within(card).getByRole("list");
    // Class-contract guard only — jsdom has no layout engine, so real geometry
    // is checked manually (docs/22 §8d). This keeps the scroll container from
    // being removed, and proves the full list renders (not sliced/truncated).
    expect(list.className).toContain("overflow-y-auto");
    expect(list.className).toContain("flex-1");
    expect(within(card).getAllByRole("listitem")).toHaveLength(30);
  });

  it("shows simmering stoves beside the frying one", async () => {
    stubFetch({ ...HANDLERS });
    renderHouse();
    await screen.findByRole("button", { name: /Open task 1/ });
    // concurrency 4, one active → three simmering stoves.
    expect(screen.getByText("stove 2 · idle")).toBeInTheDocument();
    expect(screen.getByText("stove 4 · idle")).toBeInTheDocument();
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
    await userEvent.click(await screen.findByRole("button", { name: /Open task 1/ }));
    expect(await screen.findByText("detail page")).toBeInTheDocument();
  });

  it("badges the stove when its task needs you", async () => {
    stubFetch({ ...HANDLERS });
    const { container } = renderHouse([{ ...TASK, attention: "needs_you" }]);
    await screen.findByRole("button", { name: /Open task 1/ });
    // The card is a non-interactive container (no nested button semantics);
    // scope to it via the station surface.
    const stove = screen
      .getByRole("button", { name: /Open task 1/ })
      .closest(".surface") as HTMLElement;
    expect(within(stove).getByText("needs you")).toBeInTheDocument();
    expect(container.querySelector(".brew-needs-you")).not.toBeNull();
  });

  it("pins the cook and ingredients to a frying stove", async () => {
    stubFetch({ ...HANDLERS });
    renderHouse();
    await screen.findByRole("button", { name: /Open task 1/ });
    // sec rides the Fixer agent frying task #1 → pinned under the stove.
    expect(screen.getByTitle("Security")).toBeInTheDocument();
  });

  it("says when all karhais are simmering with no orders", async () => {
    stubFetch({ ...HANDLERS });
    renderHouse([]);
    expect(await screen.findByText(/all karhais simmering/)).toBeInTheDocument();
  });

  it("pantries skills as ingredients and marks the one in the karhai", async () => {
    stubFetch({ ...HANDLERS });
    const { container } = renderHouse();
    await screen.findByRole("region", { name: "Ingredients" });
    expect(screen.getByText("Security")).toBeInTheDocument();
    expect(screen.getByText("Docs")).toBeInTheDocument();
    // sec rides the Fixer agent frying task #1 → glowing ingredient.
    expect(screen.getByText("in the karhai")).toBeInTheDocument();
    expect(container.querySelector(".brew-ingredient-live")).not.toBeNull();
  });

  it("rails cooks with their karhai when frying and resting when idle", async () => {
    stubFetch({ ...HANDLERS });
    renderHouse();
    const cooks = await screen.findByRole("region", { name: "Cooks" });
    expect(within(cooks).getByText("Fixer")).toBeInTheDocument();
    expect(within(cooks).getByText("karhai 1")).toBeInTheDocument();
  });

  it("rests cooks when nothing fries", async () => {
    stubFetch({ ...HANDLERS });
    renderHouse([]);
    const cooks = await screen.findByRole("region", { name: "Cooks" });
    expect(within(cooks).getByText("resting")).toBeInTheDocument();
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
    // Screen names render repo-qualified so identical names stay distinguishable.
    expect(await screen.findByText("owner/repo · Nightly audit")).toBeInTheDocument();
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
    expect(await screen.findByRole("button", { name: /Open task 2/ })).toBeInTheDocument();
    expect(screen.getAllByText("samosa").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("pakora").length).toBeGreaterThanOrEqual(1);
  });

  it("strike-a-match calls back to start a new task", async () => {
    stubFetch({ ...HANDLERS });
    const onNewTask = vi.fn();
    render(
      <MemoryRouter>
        <BrewHouse tasks={[]} repos={REPOS as never} onNewTask={onNewTask} />
      </MemoryRouter>
    );
    await userEvent.click(await screen.findByRole("button", { name: /Start a task on stove 1/ }));
    expect(onNewTask).toHaveBeenCalledTimes(1);
  });

  it("tickets queued orders above the karhais", async () => {
    stubFetch({ ...HANDLERS });
    renderHouse([{ ...TASK, id: 2, type: "issue_fix", status: "queued", prompt: "fix it" }]);
    const tickets = await screen.findByRole("group", { name: "Queued tasks" });
    expect(within(tickets).getByText("fix it")).toBeInTheDocument();
  });

  it("serving counter links recent dones to their tasks", async () => {
    stubFetch({ ...HANDLERS });
    renderHouse([{ ...TASK, id: 9, status: "done", prompt: "fixed" }]);
    const counter = await screen.findByRole("group", { name: "Recent tasks" });
    const link = within(counter).getByRole("link", { name: /#9/ });
    expect(link).toHaveAttribute("href", "/tasks/9");
  });

  it("boards today stats and the 7-day chart", async () => {
    stubFetch({ ...HANDLERS });
    const doneToday = {
      ...TASK,
      id: 9,
      status: "done",
      prompt: "fixed",
      attention: "working",
      created_at: new Date(Date.now() - 600_000).toISOString(),
      updated_at: new Date().toISOString(),
    };
    renderHouse([TASK, doneToday]);
    const stats = await screen.findByRole("region", { name: "Shop stats" });
    expect(within(stats).getByText("served")).toBeInTheDocument();
    expect(within(stats).getByRole("img", { name: /Tasks served per day/ })).toBeInTheDocument();
  });

  it("menu board offers orders with their snack kind", async () => {
    stubFetch({ ...HANDLERS });
    const onNewTask = vi.fn();
    render(
      <MemoryRouter>
        <BrewHouse tasks={[]} repos={REPOS as never} onNewTask={onNewTask} />
      </MemoryRouter>
    );
    const menu = await screen.findByRole("group", { name: "Today's menu" });
    expect(within(menu).getByText("Today's menu")).toBeInTheDocument();
    await userEvent.click(within(menu).getByRole("button", { name: /pakora.*pr_review/ }));
    expect(onNewTask).toHaveBeenCalledTimes(1);
    expect(onNewTask).toHaveBeenCalledWith("pakora");
  });

  it("menu strip sits above the karhais in one row", async () => {
    stubFetch({ ...HANDLERS });
    const { container } = renderHouse([]);
    await screen.findByRole("group", { name: "Today's menu" });
    const floor = container.querySelector('section[aria-label="Karhais (running tasks)"]');
    const menu = container.querySelector('[aria-label="Today\'s menu"]');
    const grid = floor?.querySelector(".grid");

    expect(floor).not.toBeNull();
    expect(menu).not.toBeNull();
    expect(grid).not.toBeNull();
    // The strip lives inside the floor section and precedes the stove grid.
    expect(floor!.contains(menu!)).toBe(true);
    expect(menu!.compareDocumentPosition(grid!)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it("burners deep-link to the queue concurrency setting", async () => {
    stubFetch({ ...HANDLERS });
    renderHouse();
    await screen.findByRole("button", { name: /Open task 1/ });
    const link = screen.getByRole("link", { name: /4 slots/ });
    expect(link).toHaveAttribute("href", "/settings?section=queue");
  });

  it("stoves open via keyboard Enter", async () => {
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
    const stove = await screen.findByRole("button", { name: /Open task 1/ });
    stove.focus();
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByText("detail page")).toBeInTheDocument();
  });

  it("stoves seat running tasks before queued ones", async () => {
    stubFetch({ ...HANDLERS });
    renderHouse([
      { ...TASK, id: 2, type: "issue_fix", status: "queued", prompt: "fix it" },
      { ...TASK, id: 1, status: "running", prompt: "brew the fix" },
    ]);
    const stoves = await screen.findAllByRole("button", { name: /Open task/ });
    expect(stoves[0]).toHaveAccessibleName(/Open task 1/);
    expect(stoves[1]).toHaveAccessibleName(/Open task 2/);
  });

  it("stats count spoiled tasks and dash the average with no dones", async () => {
    stubFetch({ ...HANDLERS });
    renderHouse([
      { ...TASK, id: 4, status: "failed", prompt: "burnt", updated_at: new Date().toISOString() },
    ]);
    const stats = await screen.findByRole("region", { name: "Shop stats" });
    expect(within(stats).getByText("spoiled")).toBeInTheDocument();
    expect(within(stats).getByText("—")).toBeInTheDocument();
  });

  it("displays task prompt, repo name, and live step message on frying station", async () => {
    stubFetch({ ...HANDLERS });
    const taskWithTelemetry = {
      ...TASK,
      id: 1,
      prompt: "Add mission control enhancements",
      run: {
        id: 11,
        started_at: "2026-09-07T00:00:30",
        steps: [
          { type: "message", text: "Analyzing mission control UI", ts: "2026-09-07T00:01:00Z" },
          { type: "tool_call", text: "view_file", ts: "2026-09-07T00:01:10Z" },
        ],
      },
    };
    render(
      <MemoryRouter>
        <BrewHouse
          tasks={[taskWithTelemetry] as never}
          repos={REPOS as never}
          onNewTask={() => {}}
        />
      </MemoryRouter>
    );
    const stove = (
      await screen.findByRole("button", { name: /Open task 1/ })
    ).closest(".surface") as HTMLElement;
    expect(within(stove).getByText("Add mission control enhancements")).toBeInTheDocument();
    expect(within(stove).getByText("repo")).toBeInTheDocument();
    expect(within(stove).getByText("Analyzing mission control UI")).toBeInTheDocument();
    expect(within(stove).getByRole("img", { name: "tool-call activity" })).toBeInTheDocument();
  });

  it("allows cancelling in-flight task directly from the station", async () => {
    stubFetch({ ...HANDLERS });
    const onCancel = vi.fn();
    render(
      <MemoryRouter>
        <BrewHouse
          tasks={[TASK] as never}
          repos={REPOS as never}
          onNewTask={() => {}}
          onCancel={onCancel}
        />
      </MemoryRouter>
    );
    const cancelBtn = await screen.findByRole("button", { name: "cancel" });
    expect(cancelBtn).toBeInTheDocument();
    await userEvent.click(cancelBtn);
    expect(onCancel).toHaveBeenCalledWith(1);
  });

  it("displays spoiled tasks on the serving counter and allows inspection", async () => {
    stubFetch({ ...HANDLERS });
    const doneTask = { ...TASK, id: 8, status: "done", prompt: "clean build" };
    const failedTask = { ...TASK, id: 9, status: "failed", prompt: "test failed" };
    render(
      <MemoryRouter>
        <BrewHouse
          tasks={[doneTask, failedTask] as never}
          repos={REPOS as never}
          onNewTask={() => {}}
        />
      </MemoryRouter>
    );
    const counter = await screen.findByRole("group", { name: "Recent tasks" });
    expect(within(counter).getByText("Served")).toBeInTheDocument();
    expect(within(counter).getByText("Spoiled (did not finish)")).toBeInTheDocument();
    expect(within(counter).getByRole("link", { name: /#8/ })).toHaveAttribute("href", "/tasks/8");
    expect(within(counter).getByRole("link", { name: /#9/ })).toHaveAttribute("href", "/tasks/9");
  });

  it("displays PR link on station when PR is created", async () => {
    stubFetch({ ...HANDLERS });
    const taskWithPr = {
      ...TASK,
      pr_number: 42,
    };
    render(
      <MemoryRouter>
        <BrewHouse tasks={[taskWithPr] as never} repos={REPOS as never} onNewTask={() => {}} />
      </MemoryRouter>
    );
    const prLink = await screen.findByRole("link", { name: /PR #42/ });
    expect(prLink).toHaveAttribute("href", "https://github.com/owner/repo/pull/42");
  });

  it("highlights stove when hovering an active cook", async () => {
    stubFetch({ ...HANDLERS });
    const { container } = render(
      <MemoryRouter>
        <BrewHouse tasks={[TASK] as never} repos={REPOS as never} onNewTask={() => {}} />
      </MemoryRouter>
    );
    const cooks = await screen.findByRole("region", { name: "Cooks" });
    const cookCard = within(cooks).getByText("Fixer").closest("li")!;
    await userEvent.hover(cookCard);
    // Stove 1 gets the highlighted ring
    expect(container.querySelector(".ring-2.ring-syrup-500\\/60")).not.toBeNull();
    await userEvent.unhover(cookCard);
    expect(container.querySelector(".ring-2.ring-syrup-500\\/60")).toBeNull();
  });

  it("renders the full-width panoramic audit radar marquee", async () => {
    stubFetch({ ...HANDLERS });
    render(
      <MemoryRouter>
        <BrewHouse tasks={[TASK] as never} repos={REPOS as never} onNewTask={() => {}} />
      </MemoryRouter>
    );
    const radar = await screen.findByRole("group", { name: "Audit radar" });
    expect(within(radar).getByText("Audit radar")).toBeInTheDocument();
    expect(within(radar).getByText("1 screen")).toBeInTheDocument();
    expect(within(radar).getAllByText("Leaky token log").length).toBe(2);
  });

  it("renders the kitchen wire comms log with filtering", async () => {
    stubFetch({ ...HANDLERS });
    render(
      <MemoryRouter>
        <BrewHouse tasks={[TASK] as never} repos={REPOS as never} onNewTask={() => {}} />
      </MemoryRouter>
    );
    const wire = await screen.findByRole("region", { name: "Kitchen wire" });
    expect(within(wire).getByText("Kitchen wire (activity)")).toBeInTheDocument();
    expect(within(wire).getByText(/Frying jalebi/)).toBeInTheDocument();
    // Test filter toggle
    await userEvent.click(within(wire).getByRole("button", { name: "live" }));
    expect(within(wire).getByText(/Frying jalebi/)).toBeInTheDocument();
    await userEvent.click(within(wire).getByRole("button", { name: "alerts" }));
    expect(within(wire).getByText(/Leaky token log/)).toBeInTheDocument();
  });
});
