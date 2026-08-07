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
    run: null,
    followups: [],
  },
];

const REPOS = [
  {
    id: 1,
    full_name: "owner/repo",
    default_branch: "main",
    clone_url: "https://x.git",
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
  "/api/github/tokens": {
    default: null,
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
});
