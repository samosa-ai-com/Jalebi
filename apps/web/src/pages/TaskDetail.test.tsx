import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import TaskDetail from "./TaskDetail";

const RUN = {
  id: 1,
  seq: 1,
  session_id: "ses_1",
  cli: "opencode",
  model: "m1",
  pat_name: null,
  status: "running",
  started_at: "2026-08-06T10:01:00",
  finished_at: null,
  steps: [{ type: "message", text: "scanning repo", ts: "2026-08-06T10:01:01" }],
};

const TASK = {
  id: 7,
  type: "freeform",
  repo_id: 1,
  repo_full_name: "owner/repo",
  source_branch: "main",
  target_branch: "main",
  model: "m1",
  cli: null,
  pat_name: null,
  prompt: "fix the bug",
  status: "running",
  timeout_minutes: 30,
  retry_count: 0,
  pr_number: null,
  issues: [],
  prs: [],
  created_at: "2026-08-06T10:00:00",
  updated_at: "2026-08-06T10:01:00",
  run: RUN,
  followups: [],
};

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

  function stubFetch(task: Record<string, unknown>) {
    const fetchMock = vi.fn(async (url: string, _init?: RequestInit) => {
      if (url.endsWith("/runs")) {
        return { ok: true, json: async () => [task.run ?? RUN] };
      }
      if (url.includes("/api/tasks")) {
        return { ok: true, json: async () => task };
      }
      if (url.includes("/api/github/tokens")) {
        return { ok: true, json: async () => ({ default: null, items: [] }) };
      }
      if (url.includes("/api/models")) {
        return { ok: true, json: async () => ({ cli: "opencode", models: ["m1"] }) };
      }
      return { ok: true, json: async () => REPOS };
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

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
    stubFetch(TASK);
    vi.stubGlobal("EventSource", FakeEventSource);

    renderDetail();
    expect(await screen.findByText("fix the bug")).toBeInTheDocument();
    expect(screen.getAllByText("scanning repo").length).toBeGreaterThan(0);
    expect(screen.getByText("running")).toBeInTheDocument();
  });

  it("streams live events into the console", async () => {
    stubFetch(TASK);
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
      run: { ...RUN, status: "done" },
      followups: [],
    };
    const fetchMock = stubFetch(doneTask);

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

  it("shows captured artifacts with preview and download", async () => {
    const withArtifacts = {
      ...TASK,
      status: "done",
      run: {
        ...RUN,
        status: "done",
        artifacts: [
          { id: 1, path: "logs/build.log", size: 2048, created_at: "2026-08-06T10:01:00" },
          { id: 2, path: "out/shot.png", size: 51200, created_at: "2026-08-06T10:01:00" },
        ],
      },
    };
    stubFetch(withArtifacts);

    renderDetail();
    expect(await screen.findByText(/Artifacts/)).toBeInTheDocument();

    expect(screen.getByText("2.0 KB")).toBeInTheDocument();
    expect(screen.getByText("out/shot.png")).toBeInTheDocument();

    const download = screen.getAllByText("download")[0];
    expect(download.closest("a")).toHaveAttribute(
      "href",
      "/api/tasks/7/artifacts/1/download"
    );
  });

  it("shows Cancel for a queued task", async () => {
    const queuedTask = { ...TASK, status: "queued" };
    stubFetch(queuedTask);
    renderDetail();
    expect(await screen.findByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("shows Re-run for a cancelled task", async () => {
    const cancelledTask = {
      ...TASK,
      status: "cancelled",
      run: { ...RUN, status: "cancelled" },
    };
    stubFetch(cancelledTask);
    renderDetail();
    expect(await screen.findByRole("button", { name: "Re-run" })).toBeInTheDocument();
  });

  it("polls after a follow-up until the new run starts", async () => {
    const doneTask = {
      ...TASK,
      status: "done",
      run: { ...RUN, status: "done" },
      followups: [],
    };
    const runningTask = {
      ...TASK,
      status: "running",
      run: { ...RUN, id: 99, status: "running" },
      followups: [{ id: 1, body: "do more", pat_name: null, model: null, created_at: "2026-08-06T10:02:00" }],
    };
    let getCount = 0;
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/runs")) {
        return { ok: true, json: async () => [doneTask.run ?? RUN] };
      }
      if (url.includes("/api/tasks") && init?.method === "POST") {
        return { ok: true, json: async () => doneTask };
      }
      if (url.includes("/api/github/tokens")) {
        return { ok: true, json: async () => ({ default: null, items: [] }) };
      }
      if (url.includes("/api/models")) {
        return { ok: true, json: async () => ({ cli: "opencode", models: ["m1"] }) };
      }
      if (url.includes("/api/repos")) {
        return { ok: true, json: async () => REPOS };
      }
      getCount += 1;
      const payload = getCount >= 2 ? runningTask : doneTask;
      return { ok: true, json: async () => payload };
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
