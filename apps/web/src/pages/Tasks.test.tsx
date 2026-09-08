import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import Tasks from "./Tasks";

const TASKS = [
  {
    id: 1,
    type: "freeform",
    repo_id: 1,
    repo_full_name: "owner/repo",
    source_branch: "main",
    target_branch: "main",
    model: null,
    cli: null,
    pat_name: null,
    prompt: "do the thing",
    status: "done",
    timeout_minutes: 30,
    retry_count: 0,
    pr_number: null,
    issues: [],
    prs: [],
    created_at: "2026-08-06T10:00:00",
    updated_at: "2026-08-06T10:05:00",
    attention: "needs_you",
    run: null,
    followups: [],
  },
];

const REPOS = [
  {
    id: 1,
    full_name: "owner/repo",
    default_branch: "main",
    connected: true,
    webhook_registered: false,
    poll_fallback: false,
    check_runs_enabled: false,
    last_checked_at: null,
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

const DEFAULT_HANDLERS = {
  "/api/tasks": TASKS,
  "/api/repos": REPOS,
  "/api/models": { cli: "opencode", models: ["opencode-go/deepseek-v4-flash"] },
  "/api/settings": { default_backend: "opencode", default_model: "opencode-go/deepseek-v4-flash" },
  "/api/github/tokens": {
    accounts: [
      {
        name: "work",
        login: "acct2",
        masked: "ghp_****",
        token_type: "classic",
        granted_scopes: ["repo"],
        missing_scopes: [],
        note: null,
        valid: true,
        error: null,
      },
    ],
  },
  "/api/github/context": { issues: [], prs: [], branches: ["main", "dev"] },
  "/api/backends": { enabled: ["opencode", "codex", "claude"], default: "opencode" },
  "/api/screenings": [],
};

describe("Tasks", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("lists tasks with status and repo", async () => {
    stubFetch({ ...DEFAULT_HANDLERS });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    expect(await screen.findByText("do the thing")).toBeInTheDocument();
    expect(screen.getAllByText("owner/repo").length).toBeGreaterThan(0);
    expect(screen.getByText("done")).toBeInTheDocument();
  });

  it("creates a task and reloads", async () => {
    const fetchMock = stubFetch({ ...DEFAULT_HANDLERS, "/api/tasks": [] });

    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    await screen.findByText("New task");

    await userEvent.type(screen.getByPlaceholderText("Instructions…"), "implement feature");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        (call) => call[0] === "/api/tasks" && call[1]?.method === "POST"
      );
      expect(postCall).toBeTruthy();
      const body = JSON.parse(postCall![1]!.body as string) as Record<string, unknown>;
      expect(body).toMatchObject({ repo_id: 1, type: "freeform", prompt: "implement feature" });
    });
  });

  it("filters the queue by status", async () => {
    const mixed = [
      { ...TASKS[0], id: 1, status: "running", prompt: "running one" },
      { ...TASKS[0], id: 2, status: "done", prompt: "done one" },
    ];
    stubFetch({ ...DEFAULT_HANDLERS, "/api/tasks": mixed });

    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    expect(await screen.findByText("running one")).toBeInTheDocument();
    expect(screen.getByText("done one")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Running \(\d+\)/ }));

    expect(screen.getByText("running one")).toBeInTheDocument();
    expect(screen.queryByText("done one")).not.toBeInTheDocument();
  });

  it("fetches GitHub context with the selected repo's account", async () => {
    const namedRepos = [{ ...REPOS[0], pat_name: "work" }];
    const fetchMock = stubFetch({ ...DEFAULT_HANDLERS, "/api/repos": namedRepos });

    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );

    await waitFor(() => {
      const ctxCall = fetchMock.mock.calls.find((call) =>
        String(call[0]).includes("/api/github/context")
      );
      expect(ctxCall).toBeTruthy();
      expect(String(ctxCall![0])).toContain("account=work");
    });
  });

  it("issue_fix shows a single target-branch picker", async () => {
    stubFetch({ ...DEFAULT_HANDLERS });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );

    await screen.findByText("New task");
    await userEvent.selectOptions(screen.getByLabelText("Task type"), "issue_fix");
    expect(
      await screen.findByLabelText("Target branch (worktree base / PR base)")
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Source branch")).not.toBeInTheDocument();
  });

  it("pr_review hides both branch pickers", async () => {
    stubFetch({ ...DEFAULT_HANDLERS });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );

    await screen.findByText("New task");
    await userEvent.selectOptions(screen.getByLabelText("Task type"), "pr_review");
    expect(screen.queryByLabelText("Source branch")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Target branch (PR base)")).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText("Target branch (worktree base / PR base)")
    ).not.toBeInTheDocument();
  });

  it("freeform keeps both branch pickers", async () => {
    stubFetch({ ...DEFAULT_HANDLERS });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );

    await screen.findByText("New task");
    expect(await screen.findByLabelText("Source branch")).toBeInTheDocument();
    expect(screen.getByLabelText("Target branch (PR base)")).toBeInTheDocument();
  });

  it("freeform can link a PR (e.g. to implement its review comments)", async () => {
    stubFetch({
      ...DEFAULT_HANDLERS,
      "/api/github/context": {
        issues: [],
        prs: [
          {
            number: 1,
            title: "Phase 1",
            html_url: "u",
            state: "open",
            base: "main",
            head: "phase-1",
            author: "me",
          },
        ],
        branches: ["main", "dev"],
      },
    });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );

    await screen.findByText("New task");
    const picker = await screen.findByLabelText("Link PR (optional)");
    expect(screen.getByRole("option", { name: /#1 — Phase 1/ })).toBeInTheDocument();

    await userEvent.selectOptions(picker, "1");
    expect(picker).toHaveValue("1");
  });

  it("fork PR offers a PR-head worktree base and sends the sentinel on create", async () => {
    const fetchMock = stubFetch({
      ...DEFAULT_HANDLERS,
      "/api/github/context": {
        issues: [],
        prs: [
          {
            number: 7,
            title: "Zen fix",
            html_url: "u",
            state: "open",
            base: "main",
            head: "feat/zen",
            head_repo: "ramon/repo",
            is_fork: true,
            author: "ramon",
          },
        ],
        branches: ["main", "dev"],
      },
    });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );

    await screen.findByText("New task");
    const picker = await screen.findByLabelText("Link PR (optional)");
    await userEvent.selectOptions(picker, "7");

    const useHead = await screen.findByRole("button", {
      name: /Base the worktree on PR #7 head/,
    });
    await userEvent.click(useHead);

    const source = screen.getByLabelText("Source branch") as HTMLSelectElement;
    expect(source.value).toBe("pr/7/head");
    expect(screen.getByRole("option", { name: /PR #7 head/ })).toBeInTheDocument();

    await userEvent.type(screen.getByPlaceholderText("Instructions…"), "address reviews");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        (call) => call[0] === "/api/tasks" && call[1]?.method === "POST"
      );
      expect(postCall).toBeTruthy();
      const body = JSON.parse(postCall![1]!.body as string) as Record<string, unknown>;
      expect(body).toMatchObject({
        pr_number: 7,
        source_branch: "pr/7/head",
        target_branch: "main",
      });
    });
  });

  it("shows env-var chips and sends selected env_vars on create", async () => {
    const fetchMock = stubFetch({
      ...DEFAULT_HANDLERS,
      "/api/envvars": [
        {
          id: 1,
          name: "DATABASE_URL",
          masked: "post***",
          repo_id: null,
          repo_full_name: null,
          created_at: "2026-08-08T00:00:00",
        },
        {
          id: 2,
          name: "API_KEY",
          masked: "sk-***",
          repo_id: 1,
          repo_full_name: "owner/repo",
          created_at: "2026-08-08T00:00:00",
        },
      ],
    });

    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    await screen.findByText("New task");
    // Env vars live behind the Advanced toggle.
    await userEvent.click(screen.getByRole("button", { name: /Advanced/ }));

    expect(await screen.findByText("DATABASE_URL")).toBeInTheDocument();
    await userEvent.click(screen.getByText("DATABASE_URL"));
    await userEvent.type(screen.getByPlaceholderText("Instructions…"), "do it");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        (call) => call[0] === "/api/tasks" && call[1]?.method === "POST"
      );
      expect(postCall).toBeTruthy();
      const body = JSON.parse(postCall![1]!.body as string) as Record<string, unknown>;
      expect(body.env_vars).toEqual(["DATABASE_URL"]);
    });
  });

  it("shows the selected agent backend in the New task caption", async () => {
    stubFetch({
      ...DEFAULT_HANDLERS,
      "/api/settings": { default_backend: "codex", default_model: "gpt-5.4-mini" },
      "/api/models": { cli: "codex", models: ["gpt-5.4-mini", "gpt-5.5"] },
    });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    await screen.findByText("New task");
    // The caption only appears once /api/settings has resolved — before that,
    // the form shows "Loading defaults…" and disables submit so a task can
    // never be created with a defaulted (opencode) backend in the gap.
    await waitFor(() => {
      expect(screen.getByText("codex · runs in a local worktree")).toBeInTheDocument();
    });
    // The Backend select lives behind the Advanced toggle; changing it
    // updates the caption and the model list.
    await userEvent.click(screen.getByRole("button", { name: /Advanced/ }));
    await userEvent.selectOptions(screen.getByLabelText("Backend"), "claude");
    await waitFor(() => {
      expect(screen.getByText("claude · runs in a local worktree")).toBeInTheDocument();
    });
  });

  it("disables the submit button and shows a loading eyebrow while defaults are unresolved", async () => {
    // A /api/settings that never resolves keeps the form in the loading state.
    // /api/models etc. resolve normally so the rest of the form renders.
    let resolveSettings!: (v: unknown) => void;
    const settingsPromise = new Promise((r) => (resolveSettings = r));
    const fetchMock = vi.fn(async (url: string, _init?: RequestInit) => {
      if (url.includes("/api/settings")) {
        await settingsPromise;
        return {
          ok: true,
          json: async () => ({ default_backend: "opencode", default_model: "x" }),
        };
      }
      if (url.includes("/api/models")) {
        return { ok: true, json: async () => ({ cli: "opencode", models: ["x"] }) };
      }
      const handler = Object.entries(DEFAULT_HANDLERS).find(([n]) => url.includes(n));
      const value = handler ? handler[1] : [];
      return { ok: true, json: async () => value };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    await screen.findByText("New task");
    expect(screen.getByText("Loading defaults…")).toBeInTheDocument();
    const submit = screen.getByRole("button", { name: /Loading|Create/ });
    expect(submit).toBeDisabled();
    // The Backend select lives behind the Advanced toggle and is disabled
    // until defaults resolve — a user can't pick a backend that the form
    // hasn't computed the model list for yet.
    await userEvent.click(screen.getByRole("button", { name: /Advanced/ }));
    expect(screen.getByLabelText("Backend")).toBeDisabled();

    // Resolve settings → the form comes alive.
    resolveSettings(null);
    await waitFor(() => {
      expect(screen.getByText("opencode · runs in a local worktree")).toBeInTheDocument();
    });
    // Type a prompt so the submit button's other disabled-conditions clear.
    await userEvent.type(screen.getByPlaceholderText("Instructions…"), "do the thing");
    expect(screen.getByRole("button", { name: "Create" })).not.toBeDisabled();
  });

  it("does not offer Screen finding as a manually creatable task type", async () => {
    stubFetch({ ...DEFAULT_HANDLERS });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    await screen.findByText("New task");
    const typeSelect = screen.getByLabelText("Task type") as HTMLSelectElement;
    const values = [...typeSelect.options].map((o) => o.value);
    expect(values).toContain("freeform");
    expect(values).not.toContain("screen_finding");
  });
});

// ---- Phase 4 T3.1 — Needs-you filter + attention dot + stat card ----------

describe("Tasks page (Phase 4 T3.1)", () => {
  function renderTasks() {
    return render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
  }

  it("shows the 'Needs you' filter chip and isolates needs_you rows", async () => {
    stubFetch({ "/api/tasks": TASKS });
    renderTasks();
    expect(await screen.findByRole("button", { name: /Needs you \(\d+\)/ })).toBeInTheDocument();
    // The needs_you row is visible by default.
    expect(screen.getByText("needs you")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Needs you \(\d+\)/ }));
    // The filtered list still shows the needs_you row.
    expect(screen.getByText("needs you")).toBeInTheDocument();
  });

  it("renders the 'Needs you' stat card with the correct count", async () => {
    stubFetch({ "/api/tasks": TASKS });
    renderTasks();
    // The pill carries its count; the stat card carries the bare label.
    expect(await screen.findByRole("button", { name: "Needs you (1)" })).toBeInTheDocument();
    const card = screen.getByTitle("Show needs you tasks");
    expect(card).toBeInTheDocument();
    expect(card.textContent).toContain("1");
  });

  it("renders the AttentionBadge next to the StatusBadge", async () => {
    stubFetch({ "/api/tasks": TASKS });
    renderTasks();
    // The AttentionBadge label is `needs you` (underscores stripped).
    expect(await screen.findByText("needs you")).toBeInTheDocument();
    // The StatusBadge label is the raw status.
    expect(screen.getByText("done")).toBeInTheDocument();
  });

  it("renders the Dismiss button on needs_you rows and allows dismissing attention", async () => {
    stubFetch({
      "/api/tasks/1/dismiss-attention": { ...TASKS[0], attention: "done" },
      "/api/tasks": TASKS,
    });
    renderTasks();
    const dismissBtn = await screen.findByRole("button", { name: "Dismiss" });
    expect(dismissBtn).toBeInTheDocument();
    await userEvent.click(dismissBtn);
    // After dismiss, the task's attention becomes "done" and the dismiss button is removed
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Dismiss" })).not.toBeInTheDocument();
    });
  });

  it("renders dependency badges and the blocked pill for a blocked task", async () => {
    const blocked = {
      ...TASKS[0],
      id: 3,
      status: "blocked",
      depends_on: [1],
      blocked_by: [1],
      blocking: [4],
      blocked: true,
    };
    stubFetch({ "/api/tasks": [blocked] });
    renderTasks();
    expect(await screen.findByText("⛔ blocked")).toBeInTheDocument();
    expect(screen.getByText("depends on #1")).toBeInTheDocument();
    expect(screen.getByText("blocks #4")).toBeInTheDocument();
    expect(screen.getByText("blocked")).toBeInTheDocument();
  });

  it("renders no dependency badges for a task without edges", async () => {
    stubFetch({ "/api/tasks": TASKS });
    renderTasks();
    await screen.findByText("do the thing");
    expect(screen.queryByText("⛔ blocked")).not.toBeInTheDocument();
    expect(screen.queryByText(/depends on #/)).not.toBeInTheDocument();
  });
});

// ---- Phase 4 T4.4 — running-now panel contract --------------------------

describe("Tasks page (Phase 4 T4.4)", () => {
  it("renders one card per queued/running task with PR links (no cap)", async () => {
    const many = [1, 2, 3, 4, 5, 6].map((i) => ({
      ...TASKS[0],
      id: i,
      status: i % 2 ? "running" : "queued",
      prompt: `job ${i}`,
      prs: [10 + i],
      pr_number: null,
      run: null,
    }));
    stubFetch({ ...DEFAULT_HANDLERS, "/api/tasks": many });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    for (let i = 1; i <= 6; i++) await screen.findByText(`job ${i}`);
    // One PR anchor per card (titles are unique to cards).
    for (let i = 1; i <= 6; i++) {
      const cardLink = screen.getByTitle(`PR #${10 + i} on owner/repo`);
      expect(cardLink).toHaveAttribute("href", `https://github.com/owner/repo/pull/${10 + i}`);
    }
  });

  it("renders PR numbers with a single # in task rows", async () => {
    stubFetch({ ...DEFAULT_HANDLERS, "/api/tasks": [{ ...TASKS[0], prs: [5] }] });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    await screen.findByText("do the thing");
    expect(screen.getByText("#5")).toBeInTheDocument();
    expect(screen.queryByText("##5")).not.toBeInTheDocument();
  });
});

// ---- Phase 4 T5.3 — table sticky header + bounded scroll ---------------
describe("Tasks page (Phase 4 T5.3)", () => {
  it("wraps the table in a bounded scroll container with a sticky header", async () => {
    const t1 = { ...TASKS[0] };
    const t2 = { ...TASKS[0], id: 2, prompt: "another one" };
    stubFetch({ "/api/tasks": [t1, t2] });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    await screen.findByText("do the thing");
    const table = screen.getByRole("table");
    const wrapper = table.parentElement as HTMLElement;
    expect(wrapper.className).toContain("overflow-y-auto");
    expect(wrapper.className).toContain("max-h-[60vh]");
    const thead = table.querySelector("thead") as HTMLElement;
    expect(thead.className).toContain("sticky");
    expect(wrapper.contains(screen.getByText("do the thing"))).toBe(true);
  });
});

// ---- Queue overhaul: attention pill, cancelled, repo filter, row nav ----
describe("Tasks page (queue overhaul)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });
  it("hides the attention pill for benign attention values", async () => {
    stubFetch({
      ...DEFAULT_HANDLERS,
      "/api/tasks": [{ ...TASKS[0], status: "running", attention: "done" }],
    });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    await screen.findByText("do the thing");
    expect(screen.getAllByText("running").length).toBeGreaterThanOrEqual(1);
    // The status pill says "running" and no stray "done" pill follows it.
    expect(screen.queryByText("done")).not.toBeInTheDocument();
    expect(screen.queryByText("working")).not.toBeInTheDocument();
  });

  it("isolates cancelled tasks under their own filter", async () => {
    const mixed = [
      { ...TASKS[0], id: 1, status: "done", prompt: "done one", attention: "done" },
      { ...TASKS[0], id: 2, status: "cancelled", prompt: "cancelled one", attention: "done" },
    ];
    stubFetch({ ...DEFAULT_HANDLERS, "/api/tasks": mixed });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    expect(await screen.findByText("cancelled one")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancelled (1)" }));
    expect(screen.getByText("cancelled one")).toBeInTheDocument();
    expect(screen.queryByText("done one")).not.toBeInTheDocument();
  });

  it("filters rows by repository", async () => {
    const mixed = [
      { ...TASKS[0], id: 1, repo_full_name: "owner/repo", prompt: "first repo task" },
      { ...TASKS[0], id: 2, repo_full_name: "owner/other", prompt: "second repo task" },
    ];
    stubFetch({ ...DEFAULT_HANDLERS, "/api/tasks": mixed });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    expect(await screen.findByText("first repo task")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Filter by repository"), "owner/other");
    expect(screen.getByText("second repo task")).toBeInTheDocument();
    expect(screen.queryByText("first repo task")).not.toBeInTheDocument();
  });

  it("stat cards filter the list when clicked", async () => {
    const mixed = [
      { ...TASKS[0], id: 1, status: "running", prompt: "running one", attention: "done" },
      { ...TASKS[0], id: 2, status: "done", prompt: "done one", attention: "done" },
    ];
    stubFetch({ ...DEFAULT_HANDLERS, "/api/tasks": mixed });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    expect(await screen.findByText("running one")).toBeInTheDocument();
    await userEvent.click(screen.getByTitle("Show done tasks"));
    expect(screen.getByText("done one")).toBeInTheDocument();
    expect(screen.queryByText("running one")).not.toBeInTheDocument();
  });

  it("navigates to the detail page when a row is clicked", async () => {
    stubFetch({ ...DEFAULT_HANDLERS });
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="/" element={<Tasks />} />
          <Route path="/tasks/:id" element={<div>detail page</div>} />
        </Routes>
      </MemoryRouter>
    );
    expect(await screen.findByText("do the thing")).toBeInTheDocument();
    // Click the ID cell (the prompt cell toggles expand instead of nav, and
    // the repo name also matches the repo-filter option).
    await userEvent.click(screen.getByText("#1"));
    expect(await screen.findByText("detail page")).toBeInTheDocument();
  });

  it("expands a truncated prompt on click", async () => {
    stubFetch({ ...DEFAULT_HANDLERS });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    const prompt = await screen.findByText("do the thing");
    const cell = prompt.closest("td") as HTMLElement;
    expect(cell.className).toContain("truncate");
    await userEvent.click(prompt);
    expect(cell.className).not.toContain("truncate");
    // Still on the list — the click didn't navigate away.
    expect(screen.getByText("do the thing")).toBeInTheDocument();
  });

  it("clone pre-fills the form from the row", async () => {
    stubFetch({ ...DEFAULT_HANDLERS });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    await screen.findByText("do the thing");
    await userEvent.click(screen.getByTitle("Clone — pre-fill the form from this task"));
    const box = screen.getByPlaceholderText("Instructions…") as HTMLTextAreaElement;
    expect(box.value).toBe("do the thing");
  });

  it("clone preserves the row's custom branches instead of resetting to default", async () => {
    stubFetch({
      ...DEFAULT_HANDLERS,
      "/api/tasks": [
        {
          ...TASKS[0],
          source_branch: "dev",
          target_branch: "dev",
          prompt: "custom branch work",
        },
      ],
      "/api/github/context": { issues: [], prs: [], branches: ["main", "dev"] },
    });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    expect(await screen.findByText("custom branch work")).toBeInTheDocument();
    await userEvent.click(screen.getByTitle("Clone — pre-fill the form from this task"));
    // The clone remounts the form, which re-fires the context fetch. Wait
    // for the "dev" options to render (proof the fetch RESOLVED, not just
    // fired) plus a macrotask beat, so the check runs after the clobber
    // window instead of passing vacuously on the pre-fetch render.
    await screen.findAllByRole("option", { name: "dev" });
    await new Promise((r) => setTimeout(r, 50));
    expect((screen.getByLabelText("Source branch") as HTMLSelectElement).value).toBe("dev");
    expect((screen.getByLabelText("Target branch (PR base)") as HTMLSelectElement).value).toBe(
      "dev"
    );
  });

  it("running-card Cancel calls the cancel endpoint and reloads", async () => {
    const fetchMock = stubFetch({
      ...DEFAULT_HANDLERS,
      "/api/tasks": [{ ...TASKS[0], status: "running", prompt: "live job", attention: "done" }],
    });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    expect(await screen.findByText("live job")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => {
      const cancel = fetchMock.mock.calls.find(
        (call) => String(call[0]).endsWith("/api/tasks/1/cancel") && call[1]?.method === "POST"
      );
      expect(cancel).toBeTruthy();
    });
  });

  it("re-run button on a failed row calls the rerun endpoint", async () => {
    const fetchMock = stubFetch({
      ...DEFAULT_HANDLERS,
      "/api/tasks": [{ ...TASKS[0], status: "failed", prompt: "broken job", attention: "done" }],
    });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    expect(await screen.findByText("broken job")).toBeInTheDocument();
    await userEvent.click(screen.getByTitle("Re-run this task"));
    await waitFor(() => {
      const rerun = fetchMock.mock.calls.find(
        (call) => String(call[0]).endsWith("/api/tasks/1/rerun") && call[1]?.method === "POST"
      );
      expect(rerun).toBeTruthy();
    });
  });

  it("bulk-dismiss clears attention on every selected row", async () => {
    const fetchMock = stubFetch({ ...DEFAULT_HANDLERS });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    await screen.findByText("do the thing");
    await userEvent.click(screen.getByLabelText("Select task #1"));
    await userEvent.click(screen.getByRole("button", { name: "Dismiss attention" }));
    await waitFor(() => {
      const dismiss = fetchMock.mock.calls.find(
        (call) =>
          String(call[0]).endsWith("/api/tasks/1/dismiss-attention") && call[1]?.method === "POST"
      );
      expect(dismiss).toBeTruthy();
    });
  });

  it("bulk-selects rows and deletes them", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchMock = stubFetch({ ...DEFAULT_HANDLERS });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    await screen.findByText("do the thing");
    await userEvent.click(screen.getByLabelText("Select task #1"));
    expect(screen.getByText("1 selected")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => {
      const del = fetchMock.mock.calls.find(
        (call) => String(call[0]).endsWith("/api/tasks/1") && call[1]?.method === "DELETE"
      );
      expect(del).toBeTruthy();
    });
    confirmSpy.mockRestore();
  });

  it("shows a Refresh button and an updated-ago stamp", async () => {
    const fetchMock = stubFetch({ ...DEFAULT_HANDLERS });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    await screen.findByText("do the thing");
    expect(screen.getByText(/updated just now/)).toBeInTheDocument();
    const before = fetchMock.mock.calls.filter((c) => c[0] === "/api/tasks").length;
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => {
      const after = fetchMock.mock.calls.filter((c) => c[0] === "/api/tasks").length;
      expect(after).toBeGreaterThan(before);
    });
  });

  it("collapses advanced options behind a toggle", async () => {
    stubFetch({ ...DEFAULT_HANDLERS });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    await screen.findByText("New task");
    expect(screen.queryByLabelText("Backend")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Advanced/ }));
    expect(screen.getByLabelText("Backend")).toBeInTheDocument();
  });

  it("selecting a PR auto-sets source to its head and target to its base", async () => {
    stubFetch({
      ...DEFAULT_HANDLERS,
      "/api/github/context": {
        issues: [],
        prs: [
          {
            number: 3,
            title: "Feature",
            html_url: "u",
            state: "open",
            base: "dev",
            head: "feature-x",
            author: "me",
          },
        ],
        branches: ["main", "dev", "feature-x"],
      },
    });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    await screen.findByText("New task");
    const picker = await screen.findByLabelText("Link PR (optional)");
    await userEvent.selectOptions(picker, "3");
    expect((screen.getByLabelText("Source branch") as HTMLSelectElement).value).toBe("feature-x");
    expect((screen.getByLabelText("Target branch (PR base)") as HTMLSelectElement).value).toBe(
      "dev"
    );
  });

  it("pr_review submits without instructions, sending a default prompt", async () => {
    const fetchMock = stubFetch({
      ...DEFAULT_HANDLERS,
      "/api/github/context": {
        issues: [],
        prs: [
          {
            number: 4,
            title: "Fix",
            html_url: "u",
            state: "open",
            base: "main",
            head: "fix",
            author: "me",
          },
        ],
        branches: ["main", "fix"],
      },
    });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    await screen.findByText("New task");
    await userEvent.selectOptions(screen.getByLabelText("Task type"), "pr_review");
    expect(screen.getByText(/optional — the reviewer already knows/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Pull request"), "4");
    // No instructions typed — Create must still be enabled.
    const create = screen.getByRole("button", { name: "Create" });
    expect(create).not.toBeDisabled();
    await userEvent.click(create);
    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        (call) => call[0] === "/api/tasks" && call[1]?.method === "POST"
      );
      expect(postCall).toBeTruthy();
      const body = JSON.parse(postCall![1]!.body as string) as Record<string, unknown>;
      expect(body).toMatchObject({ type: "pr_review", pr_number: 4, prompt: "Review PR #4." });
    });
  });

  it("mission order buttons flip back with the matching type pre-selected", async () => {
    stubFetch({
      ...DEFAULT_HANDLERS,
      "/api/settings": { default_backend: "opencode", default_model: "x", concurrency: 4 },
      "/api/agents": [],
      "/api/skills": [],
      "/api/backends": { backends: ["opencode"], enabled: ["opencode"], default: "opencode" },
      "/api/screenings": [],
      "/api/screenings/findings": [],
    });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    await screen.findByText("New task");
    await userEvent.click(screen.getByRole("button", { name: "Mission control" }));
    await userEvent.click(await screen.findByRole("button", { name: /samosa.*issue_fix/ }));
    await waitFor(() => {
      expect((screen.getByLabelText("Task type") as HTMLSelectElement).value).toBe("issue_fix");
    });
  });

  it("shows a creation confirmation linking to the new task", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/tasks" && init?.method === "POST") {
        return { ok: true, json: async () => ({ ...TASKS[0], id: 99 }) };
      }
      const handler = Object.entries(DEFAULT_HANDLERS).find(([n]) => url.includes(n));
      const value = handler ? handler[1] : [];
      return { ok: true, json: async () => value };
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    await screen.findByText("New task");
    await userEvent.type(screen.getByPlaceholderText("Instructions…"), "fresh work");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    const link = await screen.findByRole("link", { name: "#99" });
    expect(link).toHaveAttribute("href", "/tasks/99");
  });

  it("shows Back to Mission control banner and switches view when clicked", async () => {
    stubFetch(DEFAULT_HANDLERS);
    render(
      <MemoryRouter
        initialEntries={[
          { pathname: "/", search: "?view=queue&from=mission", state: { from: "mission" } },
        ]}
      >
        <Tasks />
      </MemoryRouter>
    );
    const backBtn = await screen.findByRole("button", { name: /Back to Mission control/i });
    expect(backBtn).toBeInTheDocument();
    await userEvent.click(backBtn);
    expect(screen.getByRole("heading", { name: "Halwai shop" })).toBeInTheDocument();
  });

  it("applies a screening handoff prefill and marks findings dealt only on create", async () => {
    const prompt = 'Fix this high finding from the "Security posture" screen.';
    const fp = JSON.stringify([7, "Secret in config", "config.py", 3]);
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/tasks" && init?.method === "POST") {
        return { ok: true, json: async () => ({ ...TASKS[0], id: 99 }) };
      }
      const handler = Object.entries({ ...DEFAULT_HANDLERS, "/api/tasks": [] }).find(([n]) =>
        url.includes(n)
      );
      const value = handler ? handler[1] : [];
      return { ok: true, json: async () => value };
    });
    vi.stubGlobal("fetch", fetchMock);
    const router = createMemoryRouter([{ path: "/", element: <Tasks /> }], {
      initialEntries: [
        {
          pathname: "/",
          state: {
            prefill: { repoId: 1, type: "freeform", prompt, publishMode: "manual" },
            dealtFps: [fp],
            screeningHandoffId: "test-handoff-1",
            from: "screenings",
          },
        },
      ],
    });
    render(<RouterProvider router={router} />);
    // The prompt is injected into the form for review before anything exists.
    expect(await screen.findByDisplayValue(prompt)).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.filter((call) => call[0] === "/api/tasks" && call[1]?.method === "POST")
    ).toHaveLength(0);
    expect(
      fetchMock.mock.calls.filter(
        (call) => String(call[0]).startsWith("/api/screenings/dealt") && call[1]?.method === "POST"
      )
    ).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        (call) => call[0] === "/api/tasks" && call[1]?.method === "POST"
      );
      expect(postCall).toBeTruthy();
      const body = JSON.parse(postCall![1]!.body as string) as Record<string, unknown>;
      expect(body).toMatchObject({ repo_id: 1, type: "freeform", prompt });
    });
    // Only the successful create marks the finding dealt — via the API.
    await waitFor(() => {
      const markCall = fetchMock.mock.calls.find(
        (call) => call[0] === "/api/screenings/dealt" && call[1]?.method === "POST"
      );
      expect(markCall).toBeTruthy();
      const body = JSON.parse(markCall![1]!.body as string) as Record<string, unknown>;
      expect(body).toMatchObject({ screen_id: 7, fps: [fp] });
    });
    // The handoff entry is replace-cleared (no refresh re-inject) while
    // unrelated keys like `from` survive.
    await waitFor(() => {
      expect(router.state.location.state).toEqual({ from: "screenings" });
    });
  });

  it("still creates the task when the handoff dealt-mark fails, showing an error", async () => {
    const prompt = "Fix this high finding.";
    const fp = JSON.stringify([7, "Secret in config", "config.py", 3]);
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/tasks" && init?.method === "POST") {
        return { ok: true, json: async () => ({ ...TASKS[0], id: 99 }) };
      }
      if (String(url).startsWith("/api/screenings/dealt") && init?.method === "POST") {
        return { ok: false, json: async () => ({ error: "db locked" }) };
      }
      const handler = Object.entries({ ...DEFAULT_HANDLERS, "/api/tasks": [] }).find(([n]) =>
        url.includes(n)
      );
      const value = handler ? handler[1] : [];
      return { ok: true, json: async () => value };
    });
    vi.stubGlobal("fetch", fetchMock);
    const router = createMemoryRouter([{ path: "/", element: <Tasks /> }], {
      initialEntries: [
        {
          pathname: "/",
          state: {
            prefill: { repoId: 1, type: "freeform", prompt, publishMode: "manual" },
            dealtFps: [fp],
            screeningHandoffId: "test-handoff-2",
            from: "screenings",
          },
        },
      ],
    });
    render(<RouterProvider router={router} />);
    expect(await screen.findByDisplayValue(prompt)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    // The task itself is created…
    expect(await screen.findByRole("link", { name: "#99" })).toHaveAttribute("href", "/tasks/99");
    // …and the failed mark surfaces without blocking it.
    expect(await screen.findByText(/could not be marked dealt/)).toBeInTheDocument();
  });

  it("normalizes a cloned screen_finding type to freeform", async () => {
    const legacy = { ...TASKS[0], id: 7, type: "screen_finding", prompt: "old audit fix" };
    stubFetch({ ...DEFAULT_HANDLERS, "/api/tasks": [legacy] });
    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    await screen.findByText("old audit fix");
    await userEvent.click(screen.getByTitle(/Clone/));
    await waitFor(() => {
      expect((screen.getByLabelText("Task type") as HTMLSelectElement).value).toBe("freeform");
    });
  });
});
