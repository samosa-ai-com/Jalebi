import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import TaskDetail from "./TaskDetail";

const TASK = {
  id: 7,
  type: "freeform",
  repo_id: 1,
  model: "m1",
  cli: null,
  prompt: "fix the bug",
  status: "running",
  timeout_minutes: 30,
  retry_count: 0,
  pr_number: null,
  created_at: "2026-08-06T10:00:00",
  updated_at: "2026-08-06T10:01:00",
  run: {
    id: 1,
    seq: 1,
    session_id: "ses_1",
    cli: "opencode",
    model: "m1",
    status: "running",
    started_at: "2026-08-06T10:01:00",
    finished_at: null,
    steps: [{ type: "message", text: "scanning repo", ts: "2026-08-06T10:01:01" }],
  },
};

const REPOS = [
  { id: 1, full_name: "owner/repo", default_branch: "main", clone_url: "https://x.git" },
];

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onmessage: ((ev: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;

  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }

  close() {
    this.closed = true;
  }

  emit(payload: object) {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }
}

describe("TaskDetail", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    FakeEventSource.instances = [];
  });

  function renderDetail() {
    return render(
      <MemoryRouter initialEntries={["/tasks/7"]}>
        <Routes>
          <Route path="/tasks/:id" element={<TaskDetail />} />
        </Routes>
      </MemoryRouter>
    );
  }

  it("shows the task and its stored steps", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => ({
        ok: true,
        json: async () => (url.includes("/api/tasks") ? TASK : REPOS),
      }))
    );
    vi.stubGlobal("EventSource", FakeEventSource);

    renderDetail();
    expect(await screen.findByText("fix the bug")).toBeInTheDocument();
    expect(screen.getAllByText("scanning repo").length).toBeGreaterThan(0);
    expect(screen.getByText("running")).toBeInTheDocument();
  });

  it("streams live events into the console", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => ({
        ok: true,
        json: async () => (url.includes("/api/tasks") ? TASK : REPOS),
      }))
    );
    vi.stubGlobal("EventSource", FakeEventSource);

    renderDetail();
    await screen.findByText("fix the bug");
    await waitFor(() => expect(FakeEventSource.instances.length).toBeGreaterThan(0));

    const source = FakeEventSource.instances[FakeEventSource.instances.length - 1];
    source.emit({ type: "connected" });
    source.emit({ type: "message", text: "editing files…", ts: "2026-08-06T10:01:02" });

    expect((await screen.findAllByText("editing files…")).length).toBeGreaterThan(0);
    source.emit({ type: "stream_end" });
  });

  it("shows the follow-up composer for a terminal task and posts it", async () => {
    const doneTask = {
      ...TASK,
      status: "done",
      run: { ...TASK.run!, status: "done" },
      followups: [],
    };
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("/api/tasks") && init?.method === "POST") {
        return { ok: true, json: async () => doneTask };
      }
      return { ok: true, json: async () => (url.includes("/api/tasks") ? doneTask : REPOS) };
    });
    vi.stubGlobal("fetch", fetchMock);

    renderDetail();
    expect(await screen.findByText("Follow-up")).toBeInTheDocument();

    await userEvent.type(
      screen.getByPlaceholderText(/Address the reviewer comments/),
      "do more"
    );
    await userEvent.click(screen.getByRole("button", { name: "Send follow-up" }));

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        (call) => call[0] === "/api/tasks/7/followup" && call[1]?.method === "POST"
      );
      expect(postCall).toBeTruthy();
      expect(JSON.parse(postCall![1]!.body as string)).toEqual({ prompt: "do more" });
    });
  });

  it("shows captured artifacts with download links", async () => {
    const withArtifacts = {
      ...TASK,
      status: "done",
      run: {
        ...TASK.run!,
        status: "done",
        artifacts: [
          { id: 1, path: "logs/build.log", size: 2048, created_at: "2026-08-06T10:01:00" },
          { id: 2, path: "out/shot.png", size: 51200, created_at: "2026-08-06T10:01:00" },
        ],
      },
    };
    const fetchMock = vi.fn(async (url: string) => ({
      ok: true,
      json: async () => (url.includes("/api/tasks") ? withArtifacts : REPOS),
    }));
    vi.stubGlobal("fetch", fetchMock);

    renderDetail();
    expect(await screen.findByText("Artifacts")).toBeInTheDocument();

    const link = screen.getByText("logs/build.log");
    expect(link.closest("a")).toHaveAttribute(
      "href",
      "/api/tasks/7/artifacts/1/download"
    );
    expect(screen.getByText("2.0 KB")).toBeInTheDocument();
    expect(screen.getByText("out/shot.png")).toBeInTheDocument();
  });

  it("shows Cancel for a queued task", async () => {
    const queuedTask = { ...TASK, status: "queued" };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => ({
        ok: true,
        json: async () => (url.includes("/api/tasks") ? queuedTask : REPOS),
      }))
    );
    renderDetail();
    expect(await screen.findByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("polls after a follow-up until the new run starts", async () => {
    const doneTask = {
      ...TASK,
      status: "done",
      run: { ...TASK.run!, status: "done" },
      followups: [],
    };
    const runningTask = {
      ...TASK,
      status: "running",
      run: { ...TASK.run!, id: 99, status: "running" },
      followups: [{ id: 1, body: "do more", created_at: "2026-08-06T10:02:00" }],
    };
    let getCount = 0;
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("/api/tasks") && init?.method === "POST") {
        return { ok: true, json: async () => doneTask };
      }
      getCount += 1;
      const payload = getCount >= 2 ? runningTask : doneTask;
      return { ok: true, json: async () => (url.includes("/api/tasks") ? payload : REPOS) };
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("EventSource", FakeEventSource);

    renderDetail();
    await screen.findByText("Follow-up");
    await userEvent.type(
      screen.getByPlaceholderText(/Address the reviewer comments/),
      "do more"
    );
    await userEvent.click(screen.getByRole("button", { name: "Send follow-up" }));

    await waitFor(
      () => expect(screen.getByText("running")).toBeInTheDocument(),
      { timeout: 5000 }
    );
  });
});
