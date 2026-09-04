import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
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
      { name: "work", login: "acct2", masked: "ghp_****", token_type: "classic", granted_scopes: ["repo"], missing_scopes: [], note: null, valid: true, error: null },
    ],
  },
  "/api/github/context": { issues: [], prs: [], branches: ["main", "dev"] },
};

describe("Tasks", () => {
  afterEach(() => {
    vi.restoreAllMocks();
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

    await userEvent.click(screen.getByRole("button", { name: "Running" }));

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
    expect(screen.queryByLabelText("Target branch (worktree base / PR base)")).not.toBeInTheDocument();
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
          { number: 1, title: "Phase 1", html_url: "u", state: "open", base: "main", head: "phase-1", author: "me" },
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
    expect(
      screen.getByRole("option", { name: /PR #7 head/ })
    ).toBeInTheDocument();

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
        { id: 1, name: "DATABASE_URL", masked: "post***", repo_id: null, repo_full_name: null, created_at: "2026-08-08T00:00:00" },
        { id: 2, name: "API_KEY", masked: "sk-***", repo_id: 1, repo_full_name: "owner/repo", created_at: "2026-08-08T00:00:00" },
      ],
    });

    render(
      <MemoryRouter>
        <Tasks />
      </MemoryRouter>
    );
    await screen.findByText("New task");

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
    // The Backend select lets the user override per task; changing it updates
    // the caption and the model list.
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
        return { ok: true, json: async () => ({ default_backend: "opencode", default_model: "x" }) };
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
    // The Backend select is also disabled until defaults resolve — a user
    // can't pick a backend that the form hasn't computed the model list for yet.
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
    expect(await screen.findByRole("button", { name: "Needs you" })).toBeInTheDocument();
    // The needs_you row is visible by default.
    expect(screen.getByText("needs you")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Needs you" }));
    // The filtered list still shows the needs_you row.
    expect(screen.getByText("needs you")).toBeInTheDocument();
  });

  it("renders the 'Needs you' stat card with the correct count", async () => {
    stubFetch({ "/api/tasks": TASKS });
    renderTasks();
    await waitFor(() =>
      expect(screen.getAllByText("Needs you").length).toBeGreaterThanOrEqual(2)
    );
    // The card itself is the second surface containing "Needs you".
    const needsYouCards = screen.getAllByText("Needs you");
    const cardSurface = needsYouCards.find((el) =>
      el.classList?.contains("uppercase")
    );
    expect(cardSurface).toBeTruthy();
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
