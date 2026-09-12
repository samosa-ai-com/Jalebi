import { act, render, renderHook, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { getMissionTheme, setMissionTheme } from "../../lib/missionTheme";
import type { Repo, Task } from "../../types";
import { jobForType, JOB_LABEL, JobGlyph, JOB_ACCENT, STATUS_LABEL } from "./jobStyle";
import { OpsDeck } from "./OpsDeck";
import { useDeckMood } from "./useDeckMood";
import { WorkerPane } from "./WorkerPane";

const MOCK_TASKS: Task[] = [
  {
    id: 101,
    type: "freeform",
    repo_id: 1,
    repo_full_name: "acme/api",
    source_branch: "feat-x",
    target_branch: "main",
    agent_id: null,
    model: null,
    cli: "opencode",
    pat_name: null,
    prompt: "Implement redis caching layer",
    status: "running",
    timeout_minutes: 30,
    retry_count: 0,
    pr_number: 42,
    issues: [],
    prs: [42],
    created_at: "2026-09-11T12:00:00Z",
    updated_at: "2026-09-11T12:10:00Z",
    attention: "working",
    run: {
      id: 501,
      seq: 1,
      session_id: null,
      cli: "opencode",
      model: null,
      pat_name: null,
      status: "running",
      started_at: "2026-09-11T12:00:10Z",
      finished_at: null,
      has_diff: false,
      steps: [
        {
          type: "message",
          ts: "2026-09-11T12:01:00Z",
          text: "Configuring redis connection pool",
        },
        {
          type: "tool_call",
          ts: "2026-09-11T12:02:00Z",
          text: "write_file",
        },
      ],
    },
    followups: [],
    env_vars: [],
    check_run_id: null,
    publish_mode: null,
  },
  {
    id: 102,
    type: "issue_fix",
    repo_id: 1,
    repo_full_name: "acme/api",
    source_branch: "fix-auth",
    target_branch: "main",
    agent_id: null,
    model: null,
    cli: "claude",
    pat_name: null,
    prompt: "Fix token expiration bug",
    status: "running",
    timeout_minutes: 20,
    retry_count: 0,
    pr_number: null,
    issues: [],
    prs: [],
    created_at: "2026-09-11T12:05:00Z",
    updated_at: "2026-09-11T12:12:00Z",
    attention: "needs_you",
    run: {
      id: 502,
      seq: 1,
      session_id: null,
      cli: "claude",
      model: null,
      pat_name: null,
      status: "running",
      started_at: "2026-09-11T12:05:30Z",
      finished_at: null,
      has_diff: false,
      steps: [
        {
          type: "message",
          ts: "2026-09-11T12:06:00Z",
          text: "Should I rotate the existing secret?",
        },
      ],
    },
    followups: [],
    env_vars: [],
    check_run_id: null,
    publish_mode: null,
  },
  {
    id: 103,
    type: "pr_review",
    repo_id: 1,
    repo_full_name: "acme/api",
    source_branch: "main",
    target_branch: "main",
    agent_id: null,
    model: null,
    cli: "codex",
    pat_name: null,
    prompt: "Review PR #99",
    status: "queued",
    timeout_minutes: 15,
    retry_count: 0,
    pr_number: 99,
    issues: [],
    prs: [99],
    created_at: "2026-09-11T12:15:00Z",
    updated_at: "2026-09-11T12:15:00Z",
    attention: "working",
    run: null,
    followups: [],
    env_vars: [],
    check_run_id: null,
    publish_mode: null,
  },
  {
    id: 104,
    type: "freeform",
    repo_id: 1,
    repo_full_name: "acme/api",
    source_branch: "feat-docs",
    target_branch: "main",
    agent_id: null,
    model: null,
    cli: "opencode",
    pat_name: null,
    prompt: "Generate API reference documentation",
    status: "done",
    timeout_minutes: 30,
    retry_count: 0,
    pr_number: 100,
    issues: [],
    prs: [100],
    created_at: "2026-09-11T11:00:00Z",
    updated_at: "2026-09-11T11:20:00Z",
    attention: "done",
    run: null,
    followups: [],
    env_vars: [],
    check_run_id: null,
    publish_mode: null,
  },
  {
    id: 105,
    type: "issue_fix",
    repo_id: 1,
    repo_full_name: "acme/api",
    source_branch: "fix-crash",
    target_branch: "main",
    agent_id: null,
    model: null,
    cli: "opencode",
    pat_name: null,
    prompt: "Fix crash on invalid payload",
    status: "failed",
    timeout_minutes: 10,
    retry_count: 0,
    pr_number: null,
    issues: [],
    prs: [],
    created_at: "2026-09-11T11:30:00Z",
    updated_at: "2026-09-11T11:35:00Z",
    attention: "done",
    run: null,
    followups: [],
    env_vars: [],
    check_run_id: null,
    publish_mode: null,
  },
];

const MOCK_REPOS: Repo[] = [
  {
    id: 1,
    full_name: "acme/api",
    default_branch: "main",
    connected: true,
    webhook_registered: false,
    poll_fallback: false,
    check_runs_enabled: false,
    last_checked_at: null,
    pat_name: null,
  },
];

describe("missionTheme helper", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("defaults to 'ops' when storage is empty or invalid", () => {
    expect(getMissionTheme()).toBe("ops");
    localStorage.setItem("jalebi-mission-theme", "random");
    expect(getMissionTheme()).toBe("ops");
  });

  it("returns 'brew' only when exactly 'brew'", () => {
    localStorage.setItem("jalebi-mission-theme", "brew");
    expect(getMissionTheme()).toBe("brew");
  });

  it("persists theme correctly via setMissionTheme", () => {
    setMissionTheme("brew");
    expect(localStorage.getItem("jalebi-mission-theme")).toBe("brew");
    setMissionTheme("ops");
    expect(localStorage.getItem("jalebi-mission-theme")).toBe("ops");
  });
});

describe("jobStyle helper", () => {
  it("maps task types to coding job kinds", () => {
    expect(jobForType("freeform")).toBe("feature");
    expect(jobForType("issue_fix")).toBe("fix");
    expect(jobForType("pr_review")).toBe("review");
    expect(jobForType("unknown")).toBe("feature");
  });

  it("provides labels and accents for all job kinds", () => {
    expect(JOB_LABEL.feature).toBe("feature");
    expect(JOB_LABEL.fix).toBe("fix");
    expect(JOB_LABEL.review).toBe("review");

    expect(JOB_ACCENT.feature.text).toContain("sky");
    expect(JOB_ACCENT.fix.text).toContain("amber");
    expect(JOB_ACCENT.review.text).toContain("violet");

    expect(STATUS_LABEL.queued).toBe("queued");
    expect(STATUS_LABEL.done).toBe("shipped");
  });

  it("renders JobGlyph SVGs without crashing", () => {
    const { container: c1 } = render(<JobGlyph kind="feature" />);
    expect(c1.querySelector("svg")).toBeInTheDocument();

    const { container: c2 } = render(<JobGlyph kind="fix" />);
    expect(c2.querySelector("svg")).toBeInTheDocument();

    const { container: c3 } = render(<JobGlyph kind="review" />);
    expect(c3.querySelector("svg")).toBeInTheDocument();
  });
});

describe("WorkerPane", () => {
  it("renders idle state with prompt and spin up CTA when job is null", async () => {
    const onOrder = vi.fn();
    render(
      <WorkerPane
        core={1}
        job={null}
        now={Date.now()}
        onOpen={vi.fn()}
        onOrder={onOrder}
      />
    );

    expect(screen.getByText("CORE-01")).toBeInTheDocument();
    expect(screen.getByText(/STANDBY/i)).toBeInTheDocument();
    expect(screen.getByText(/Worker core online/i)).toBeInTheDocument();

    const btn = screen.getByRole("button", { name: /spin up a job/i });
    await userEvent.click(btn);
    expect(onOrder).toHaveBeenCalledTimes(1);
  });

  it("renders queued state with shimmer bar", () => {
    render(
      <WorkerPane
        core={2}
        job={{
          task: MOCK_TASKS[2],
          agentName: null,
          avatarUrl: null,
          skillNames: [],
          startedAtMs: null,
          timeoutMs: 15 * 60000,
          steps: 0,
          repoName: "acme/api",
        }}
        now={Date.now()}
        onOpen={vi.fn()}
        onOrder={vi.fn()}
      />
    );

    expect(screen.getByText("CORE-02")).toBeInTheDocument();
    expect(screen.getByText("#103")).toBeInTheDocument();
    expect(screen.getByText("QUEUED")).toBeInTheDocument();
  });

  it("renders running state with live logs, sparkline, and PR link", () => {
    render(
      <WorkerPane
        core={1}
        job={{
          task: MOCK_TASKS[0],
          agentName: "Architect",
          avatarUrl: null,
          skillNames: ["git-workflow"],
          startedAtMs: Date.now() - 60000,
          timeoutMs: 30 * 60000,
          steps: 2,
          repoName: "acme/api",
        }}
        now={Date.now()}
        onOpen={vi.fn()}
        onOrder={vi.fn()}
      />
    );

    expect(screen.getByText("CORE-01")).toBeInTheDocument();
    expect(screen.getByText("#101")).toBeInTheDocument();
    expect(screen.getByText(/Configuring redis connection pool/i)).toBeInTheDocument();
    expect(screen.getByText(/PR #42/i)).toBeInTheDocument();
  });

  it("renders needs_you state with awaiting input and respond button", async () => {
    const onOpen = vi.fn();
    render(
      <WorkerPane
        core={2}
        job={{
          task: MOCK_TASKS[1],
          agentName: "BugHunter",
          avatarUrl: null,
          skillNames: [],
          startedAtMs: Date.now() - 30000,
          timeoutMs: 20 * 60000,
          steps: 1,
          repoName: "acme/api",
        }}
        now={Date.now()}
        onOpen={onOpen}
        onOrder={vi.fn()}
      />
    );

    expect(screen.getByText(/awaiting input/i)).toBeInTheDocument();
    const respondBtn = screen.getByRole("button", { name: /respond/i });
    await userEvent.click(respondBtn);
    expect(onOpen).toHaveBeenCalledWith(102);
  });
});

describe("OpsDeck component", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/api/agents")) return { ok: true, json: async () => [] };
        if (url.includes("/api/skills")) return { ok: true, json: async () => [] };
        if (url.includes("/api/backends"))
          return { ok: true, json: async () => ({ backends: ["opencode"], enabled: ["opencode"] }) };
        if (url.includes("/api/settings"))
          return { ok: true, json: async () => ({ concurrency: 4 }) };
        if (url.includes("/api/screenings/findings")) return { ok: true, json: async () => [] };
        if (url.includes("/api/screenings")) return { ok: true, json: async () => [] };
        return { ok: true, json: async () => [] };
      })
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders complete Ops Deck dashboard with all rails and telemetry", async () => {
    const onNewTask = vi.fn();
    render(
      <MemoryRouter>
        <OpsDeck
          tasks={MOCK_TASKS}
          repos={MOCK_REPOS}
          onNewTask={onNewTask}
        />
      </MemoryRouter>
    );

    expect(screen.getByRole("heading", { name: "Ops Deck" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Security & Audit radar" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Worker process grid" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Runners" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Toolbelt" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Deck statistics" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Ops event stream" })).toBeInTheDocument();

    // Verify shipped & faulted bottom chips
    expect(screen.getAllByText("#104").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("#105").length).toBeGreaterThanOrEqual(1);

    // Verify quick launch button
    const featureBtn = screen.getByRole("button", { name: /new feature/i });
    await userEvent.click(featureBtn);
    expect(onNewTask).toHaveBeenCalledWith("feature");
  });

  it("does not pass queued tasks to cores; queued tasks show in pending strip and idle CTAs >= 1", () => {
    render(
      <MemoryRouter>
        <OpsDeck
          tasks={MOCK_TASKS}
          repos={MOCK_REPOS}
          onNewTask={vi.fn()}
        />
      </MemoryRouter>
    );

    // Concurrency is 4, running tasks are #101 and #102.
    // #103 is queued and must not occupy a core.
    const idleButtons = screen.getAllByRole("button", { name: /spin up a job/i });
    expect(idleButtons.length).toBeGreaterThanOrEqual(1);

    const pendingGroup = screen.getByRole("group", { name: "Pending jobs" });
    expect(pendingGroup).toBeInTheDocument();
    expect(pendingGroup).toHaveTextContent("#103");
    expect(pendingGroup).toHaveTextContent("Review PR #99");
  });

  it("keeps blocked tasks visible in the pending strip with a blocked badge", () => {
    const tasksWithBlocked: Task[] = [
      ...MOCK_TASKS,
      {
        id: 106,
        type: "issue_fix",
        repo_id: 1,
        repo_full_name: "acme/api",
        source_branch: "fix-lock",
        target_branch: "main",
        agent_id: null,
        model: null,
        cli: "opencode",
        pat_name: null,
        prompt: "Fix database deadlock",
        status: "blocked",
        blocked: true,
        timeout_minutes: 30,
        retry_count: 0,
        pr_number: null,
        issues: [],
        prs: [],
        created_at: "2026-09-11T12:00:00Z",
        updated_at: "2026-09-11T12:10:00Z",
        attention: "working",
        run: null,
        followups: [],
        env_vars: [],
        check_run_id: null,
        publish_mode: null,
      },
    ];

    render(
      <MemoryRouter>
        <OpsDeck
          tasks={tasksWithBlocked}
          repos={MOCK_REPOS}
          onNewTask={vi.fn()}
        />
      </MemoryRouter>
    );

    const pendingGroup = screen.getByRole("group", { name: "Pending jobs" });
    expect(pendingGroup).toHaveTextContent("#106");
    expect(pendingGroup).toHaveTextContent("Fix database deadlock");
    expect(pendingGroup).toHaveTextContent("blocked");
  });

  it("concurrency link preserves mission context via state", () => {
    render(
      <MemoryRouter>
        <OpsDeck
          tasks={MOCK_TASKS}
          repos={MOCK_REPOS}
          onNewTask={vi.fn()}
        />
      </MemoryRouter>
    );

    const link = screen.getByRole("link", { name: /allocated →/i });
    expect(link).toHaveAttribute("href", "/settings?section=queue");
  });

  it("WorkerPane deduplicates live log lines by seq and consecutive identical text", () => {
    const taskWithStep: Task = {
      ...MOCK_TASKS[0],
      run: {
        ...MOCK_TASKS[0].run!,
        steps: [
          {
            type: "message",
            ts: "2026-09-11T12:01:00Z",
            text: "Step one",
            seq: 1,
          },
          {
            type: "message",
            ts: "2026-09-11T12:01:05Z",
            text: "Step two without seq",
          },
          {
            type: "message",
            ts: "2026-09-11T12:01:06Z",
            text: "Step two without seq",
          },
        ],
      },
    };

    render(
      <WorkerPane
        core={1}
        job={{
          task: taskWithStep,
          agentName: "Agent",
          avatarUrl: null,
          skillNames: [],
          startedAtMs: Date.now() - 5000,
          timeoutMs: 60000,
          steps: 3,
          repoName: "acme/api",
        }}
        now={Date.now()}
        onOpen={vi.fn()}
        onOrder={vi.fn()}
      />
    );

    // Consecutive identical text without seq should be deduplicated
    const matches = screen.getAllByText("Step two without seq");
    expect(matches).toHaveLength(1);
    expect(screen.getByText("Step one")).toBeInTheDocument();
  });

  it("OpsDeck root carries data-mood and data-flash attributes", () => {
    const { container } = render(
      <MemoryRouter>
        <OpsDeck
          tasks={MOCK_TASKS}
          repos={MOCK_REPOS}
          onNewTask={vi.fn()}
        />
      </MemoryRouter>
    );

    const deckRoot = container.querySelector(".ops-deck");
    expect(deckRoot).toBeInTheDocument();
    expect(deckRoot).toHaveAttribute("data-mood", "attention");
    expect(deckRoot).toHaveAttribute("data-flash", "none");
  });

  it("restores Director mode from localStorage and persists toggles", async () => {
    localStorage.setItem("jalebi-mission-director-v1", "1");
    render(
      <MemoryRouter>
        <OpsDeck tasks={MOCK_TASKS} repos={MOCK_REPOS} onNewTask={vi.fn()} />
      </MemoryRouter>
    );

    const btn = screen.getByRole("button", { name: /director/i });
    expect(btn).toHaveAttribute("aria-pressed", "true");

    await userEvent.click(btn);
    expect(localStorage.getItem("jalebi-mission-director-v1")).toBe("0");
    localStorage.removeItem("jalebi-mission-director-v1");
  });

  it("Director mode no-ops on an idle deck (no spotlight, no dimming)", () => {
    localStorage.setItem("jalebi-mission-director-v1", "1");
    const idleOnly = MOCK_TASKS.filter((t) => t.status !== "running");
    const { container } = render(
      <MemoryRouter>
        <OpsDeck tasks={idleOnly} repos={MOCK_REPOS} onNewTask={vi.fn()} />
      </MemoryRouter>
    );

    expect(container.querySelectorAll(".ops-spot").length).toBe(0);
    const dimmed = [...container.querySelectorAll("div")].some((el) =>
      typeof el.className === "string" ? el.className.includes("scale-[0.985]") : false
    );
    expect(dimmed).toBe(false);
    localStorage.removeItem("jalebi-mission-director-v1");
  });
});

describe("useDeckMood hook", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const baseTask: Task = {
    id: 1,
    type: "freeform",
    repo_id: 1,
    repo_full_name: "acme/api",
    source_branch: "main",
    target_branch: "main",
    agent_id: null,
    model: null,
    cli: "opencode",
    pat_name: null,
    prompt: "Test task",
    status: "done",
    timeout_minutes: 10,
    retry_count: 0,
    pr_number: null,
    issues: [],
    prs: [],
    created_at: "2026-09-11T12:00:00Z",
    updated_at: "2026-09-11T12:10:00Z",
    attention: "done",
    run: null,
    followups: [],
    env_vars: [],
    check_run_id: null,
    publish_mode: null,
  };

  it("evaluates to 'idle' when there are no active tasks or recent faults", () => {
    const { result } = renderHook(() => useDeckMood([]));
    expect(result.current.mood).toBe("idle");
    expect(result.current.flash).toBe("none");
  });

  it("evaluates to 'active' when a running task exists", () => {
    const tasks: Task[] = [{ ...baseTask, id: 10, status: "running", attention: "working" }];
    const { result } = renderHook(() => useDeckMood(tasks));
    expect(result.current.mood).toBe("active");
  });

  it("evaluates to 'attention' when a task has attention === 'needs_you' (precedence over running)", () => {
    const tasks: Task[] = [
      { ...baseTask, id: 10, status: "running", attention: "working" },
      { ...baseTask, id: 11, status: "queued", attention: "needs_you" },
    ];
    const { result } = renderHook(() => useDeckMood(tasks));
    expect(result.current.mood).toBe("attention");
  });

  it("evaluates to 'fault' when a failed/timed_out/interrupted task updated within 5 minutes (precedence over attention and running)", () => {
    const now = Date.now();
    const recentIso = new Date(now - 60_000).toISOString();
    const tasks: Task[] = [
      { ...baseTask, id: 10, status: "running", attention: "working" },
      { ...baseTask, id: 11, status: "running", attention: "needs_you" },
      { ...baseTask, id: 12, status: "failed", updated_at: recentIso },
    ];
    const { result } = renderHook(() => useDeckMood(tasks));
    expect(result.current.mood).toBe("fault");
  });

  it("ignores faults older than 5 minutes and falls back to running/attention/idle", () => {
    const now = Date.now();
    const oldIso = new Date(now - 6 * 60_000).toISOString();
    const tasks: Task[] = [
      { ...baseTask, id: 10, status: "running", attention: "working" },
      { ...baseTask, id: 12, status: "failed", updated_at: oldIso },
    ];
    const { result } = renderHook(() => useDeckMood(tasks));
    expect(result.current.mood).toBe("active");
  });

  it("correctly triggers 'celebrate' flash for 2500ms when done-id set gains a member", () => {
    const initialTasks: Task[] = [{ ...baseTask, id: 20, status: "running" }];
    const { result, rerender } = renderHook(({ tasks }) => useDeckMood(tasks), {
      initialProps: { tasks: initialTasks },
    });

    expect(result.current.flash).toBe("none");

    const updatedTasks: Task[] = [{ ...baseTask, id: 20, status: "done" }];
    rerender({ tasks: updatedTasks });

    expect(result.current.flash).toBe("celebrate");

    act(() => {
      vi.advanceTimersByTime(2400);
    });
    expect(result.current.flash).toBe("celebrate");

    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(result.current.flash).toBe("none");
  });

  it("correctly triggers 'fault' flash for 2500ms when fault-id set gains a member", () => {
    const initialTasks: Task[] = [{ ...baseTask, id: 30, status: "running" }];
    const { result, rerender } = renderHook(({ tasks }) => useDeckMood(tasks), {
      initialProps: { tasks: initialTasks },
    });

    expect(result.current.flash).toBe("none");

    const updatedTasks: Task[] = [{ ...baseTask, id: 30, status: "failed" }];
    rerender({ tasks: updatedTasks });

    expect(result.current.flash).toBe("fault");

    act(() => {
      vi.advanceTimersByTime(2500);
    });
    expect(result.current.flash).toBe("none");
  });

  it("ensures the most recent transition wins and resets the timer", () => {
    const initialTasks: Task[] = [
      { ...baseTask, id: 40, status: "running" },
      { ...baseTask, id: 41, status: "running" },
    ];
    const { result, rerender } = renderHook(({ tasks }) => useDeckMood(tasks), {
      initialProps: { tasks: initialTasks },
    });

    rerender({
      tasks: [
        { ...baseTask, id: 40, status: "done" },
        { ...baseTask, id: 41, status: "running" },
      ],
    });
    expect(result.current.flash).toBe("celebrate");

    act(() => {
      vi.advanceTimersByTime(1000);
    });
    rerender({
      tasks: [
        { ...baseTask, id: 40, status: "done" },
        { ...baseTask, id: 41, status: "failed" },
      ],
    });
    expect(result.current.flash).toBe("fault");

    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(result.current.flash).toBe("fault");

    act(() => {
      vi.advanceTimersByTime(500);
    });
    expect(result.current.flash).toBe("none");
  });

  it("does not flash on the first real snapshot after an initial empty load", () => {
    const { result, rerender } = renderHook(({ tasks }) => useDeckMood(tasks), {
      initialProps: { tasks: [] as Task[] },
    });
    expect(result.current.flash).toBe("none");

    const doneTasks: Task[] = [{ ...baseTask, id: 50, status: "done" }];
    rerender({ tasks: doneTasks });
    expect(result.current.flash).toBe("none");

    // A later genuine completion still flashes.
    rerender({ tasks: [...doneTasks, { ...baseTask, id: 51, status: "done" }] });
    expect(result.current.flash).toBe("celebrate");
  });

  it("falls back from fault once the 5-minute window expires", () => {
    const now = Date.now();
    const faulted: Task = {
      ...baseTask,
      id: 60,
      status: "failed",
      updated_at: new Date(now - 60_000).toISOString(),
    };
    const { result, rerender } = renderHook(({ at }) => useDeckMood([faulted], at), {
      initialProps: { at: now },
    });
    expect(result.current.mood).toBe("fault");

    rerender({ at: now + 6 * 60_000 });
    expect(result.current.mood).toBe("idle");
  });
});

