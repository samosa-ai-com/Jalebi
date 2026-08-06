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
    model: null,
    cli: null,
    prompt: "do the thing",
    status: "done",
    timeout_minutes: 30,
    retry_count: 0,
    pr_number: null,
    created_at: "2026-08-06T10:00:00",
    updated_at: "2026-08-06T10:05:00",
    run: null,
  },
];

const REPOS = [
  { id: 1, full_name: "owner/repo", default_branch: "main", clone_url: "https://x.git" },
];

describe("Tasks", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists tasks with status and repo", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, _init?: RequestInit) => ({
        ok: true,
        json: async () => (url.includes("/api/tasks") ? TASKS : REPOS),
      }))
    );
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
    const fetchMock = vi.fn(async (url: string, _init?: RequestInit) => ({
      ok: true,
      json: async () => (url.includes("/api/tasks") ? [] : REPOS),
    }));
    vi.stubGlobal("fetch", fetchMock);

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
    const fetchMock = vi.fn(async (url: string) => ({
      ok: true,
      json: async () => (url.includes("/api/tasks") ? mixed : REPOS),
    }));
    vi.stubGlobal("fetch", fetchMock);

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
