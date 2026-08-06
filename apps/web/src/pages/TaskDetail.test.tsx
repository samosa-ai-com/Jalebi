import { render, screen, waitFor } from "@testing-library/react";
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
});
