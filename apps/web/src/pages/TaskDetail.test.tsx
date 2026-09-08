import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
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
  has_diff: false,
  steps: [{ type: "message", text: "scanning repo", ts: "2026-08-06T10:01:01" }],
};

const TASK = {
  id: 7,
  type: "freeform",
  repo_id: 1,
  repo_full_name: "owner/repo",
  source_branch: "main",
  target_branch: "main",
  agent_id: null,
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
  attention: "working",
  run: RUN,
  followups: [],
  reviewers: [],
};

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

  function stubFetch(
    task: Record<string, unknown>,
    settings: Record<string, unknown> | null = null
  ) {
    const fetchMock = vi.fn(async (url: string, _init?: RequestInit) => {
      if (url.endsWith("/runs")) {
        return { ok: true, json: async () => [task.run ?? RUN] };
      }
      if (url.includes("/files")) {
        return {
          ok: true,
          json: async () => ({ path: "", entries: [] }),
        };
      }
      if (url.includes("/api/tasks")) {
        return { ok: true, json: async () => task };
      }
      if (url.includes("/api/settings")) {
        if (settings) return { ok: true, json: async () => settings };
        return {
          ok: true,
          json: async () => ({ default_backend: "opencode", default_model: "m1" }),
        };
      }
      if (url.includes("/api/github/tokens")) {
        return { ok: true, json: async () => ({ accounts: [] }) };
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
    // Task header + single-run strip each carry a status pill.
    expect(screen.getAllByText("running").length).toBeGreaterThanOrEqual(2);
  });

  it("shows which webhook event started a triggered task", async () => {
    stubFetch({
      ...TASK,
      triggered_by: {
        delivery_id: "d-origin-1",
        event: "push",
        received_at: "2026-08-06T10:00:00",
      },
    });
    vi.stubGlobal("EventSource", FakeEventSource);

    renderDetail();
    expect(await screen.findByText("Started by")).toBeInTheDocument();
    expect(screen.getByText(/push ·/)).toBeInTheDocument();
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
    expect(screen.getByText(/Resume refreshes remote refs first/)).toBeInTheDocument();

    await userEvent.type(screen.getByPlaceholderText(/Add a regression test/), "do more");
    await userEvent.click(screen.getByRole("button", { name: "Send follow-up" }));

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        (call) => call[0] === "/api/tasks/7/followup" && call[1]?.method === "POST"
      );
      expect(postCall).toBeTruthy();
      expect(JSON.parse(postCall![1]!.body as string)).toEqual({ prompt: "do more" });
    });
  });

  it("follow-up Backend select overrides the backend and shows the fresh-session note", async () => {
    const doneTask = {
      ...TASK,
      status: "done",
      cli: "opencode",
      run: { ...RUN, status: "done" },
      followups: [],
    };
    const fetchMock = stubFetch(doneTask);

    renderDetail();
    await screen.findByText("Follow-up");

    const backend = screen.getByLabelText("Backend") as HTMLSelectElement;
    expect(backend.value).toBe("opencode");
    // No note when the backend matches the task's own.
    expect(screen.queryByText(/fresh session/)).not.toBeInTheDocument();

    await userEvent.selectOptions(backend, "codex");
    expect(screen.getByText(/fresh session/)).toBeInTheDocument();

    await userEvent.type(screen.getByPlaceholderText(/Add a regression test/), "switch backend");
    await userEvent.click(screen.getByRole("button", { name: "Send follow-up" }));

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        (call) => call[0] === "/api/tasks/7/followup" && call[1]?.method === "POST"
      );
      expect(postCall).toBeTruthy();
      expect(JSON.parse(postCall![1]!.body as string)).toEqual({
        prompt: "switch backend",
        cli: "codex",
      });
    });
  });

  it("does NOT show the fresh-session note when picking 'Reuse task backend' on an unpinned task", async () => {
    // A task without a pinned cli (cli: null) — the queue resolves to
    // default_backend ("codex" below). The "Reuse task backend" option (cli="")
    // therefore keeps the same backend; the warning must NOT appear.
    const doneTask = {
      ...TASK,
      status: "done",
      cli: null,
      run: { ...RUN, status: "done" },
      followups: [],
    };
    stubFetch(doneTask, { default_backend: "codex", default_model: "m1" });

    renderDetail();
    await screen.findByText("Follow-up");

    // Default selection is the "Reuse task backend" option (value="").
    const backend = screen.getByLabelText("Backend") as HTMLSelectElement;
    expect(backend.value).toBe("");
    // No note — the resolved backend is the same.
    expect(screen.queryByText(/fresh session/)).not.toBeInTheDocument();

    // Picker is "opencode" — different from the resolved "codex" → note shows.
    await userEvent.selectOptions(backend, "opencode");
    expect(screen.getByText(/fresh session/)).toBeInTheDocument();
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
    expect(download.closest("a")).toHaveAttribute("href", "/api/tasks/7/artifacts/1/download");
  });

  it("renders the run-end diff in the Diff section", async () => {
    const withDiff = {
      ...TASK,
      status: "done",
      run: { ...RUN, id: 5, status: "done", has_diff: true },
    };
    const fetchMock = vi.fn(async (url: string) => {
      if (url.includes("/diff")) {
        return {
          ok: true,
          json: async () => ({
            diff: "diff --git a/f.txt b/f.txt\n--- a/f.txt\n+++ b/f.txt\n@@ -1 +1 @@\n-old\n+new\n",
          }),
        };
      }
      if (url.endsWith("/runs")) {
        return { ok: true, json: async () => [withDiff.run] };
      }
      if (url.includes("/api/tasks")) {
        return { ok: true, json: async () => withDiff };
      }
      if (url.includes("/api/github/tokens")) {
        return { ok: true, json: async () => ({ accounts: [] }) };
      }
      if (url.includes("/api/models")) {
        return { ok: true, json: async () => ({ cli: "opencode", models: ["m1"] }) };
      }
      return { ok: true, json: async () => REPOS };
    });
    vi.stubGlobal("fetch", fetchMock);

    renderDetail();
    expect(await screen.findByText("Diff")).toBeInTheDocument();
    expect(await screen.findByText("f.txt")).toBeInTheDocument();
    expect(screen.getByText("+new")).toBeInTheDocument();
    expect(screen.getByText("-old")).toBeInTheDocument();
    // The improved diff view also surfaces a +/- tally (1 addition, 1 deletion).
    expect(screen.getAllByText("+1").length + screen.getAllByText("−1").length).toBeGreaterThan(0);
    expect(screen.getAllByText("+1").length).toBeGreaterThan(0);
    expect(screen.getAllByText("−1").length).toBeGreaterThan(0);
  });

  it("shows run history with status, duration, and diff/artifact markers", async () => {
    const run1 = {
      ...RUN,
      id: 1,
      seq: 1,
      status: "done",
      started_at: "2026-08-06T10:00:00",
      finished_at: "2026-08-06T10:00:30",
      has_diff: true,
    };
    const run2 = {
      ...RUN,
      id: 2,
      seq: 2,
      status: "running",
      started_at: "2026-08-06T10:01:00",
      finished_at: null,
      has_diff: false,
      model: "m2",
      artifacts: [{ id: 1, path: "logs/build.log", size: 2048, created_at: "2026-08-06T10:01:00" }],
    };
    const task = { ...TASK, run: run2 };
    const fetchMock = vi.fn(async (url: string) => {
      if (url.endsWith("/runs")) {
        return { ok: true, json: async () => [run1, run2] };
      }
      if (url.includes("/api/tasks")) {
        return { ok: true, json: async () => task };
      }
      if (url.includes("/api/github/tokens")) {
        return { ok: true, json: async () => ({ accounts: [] }) };
      }
      if (url.includes("/api/models")) {
        return { ok: true, json: async () => ({ cli: "opencode", models: ["m1"] }) };
      }
      return { ok: true, json: async () => REPOS };
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("EventSource", FakeEventSource);

    renderDetail();
    expect(await screen.findByText("Run history")).toBeInTheDocument();
    // Status badges for both runs
    expect(screen.getByText("#1")).toBeInTheDocument();
    expect(screen.getByText("#2")).toBeInTheDocument();
    expect(screen.getByText("done")).toBeInTheDocument();
    expect(screen.getAllByText("running").length).toBeGreaterThan(0);
    // Duration of the finished run is rendered
    expect(screen.getByText("30s")).toBeInTheDocument();
    // Diff marker on run 1, artifact marker on run 2
    expect(screen.getByText("diff")).toBeInTheDocument();
    expect(screen.getByText("1 artifact")).toBeInTheDocument();
  });

  it("loads the selected historical snapshot and returns to the live diff", async () => {
    const run1 = { ...RUN, id: 1, seq: 1, status: "done", has_diff: true };
    const run2 = { ...run1, id: 2, seq: 2 };
    const task = { ...TASK, status: "done", run: run2 };
    const fetchMock = stubFetch(task);
    const fallback = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/runs")) return { ok: true, json: async () => [run1, run2] };
      if (url.includes("/diff")) {
        const content = url.endsWith("/runs/1/diff") ? "historical content" : "current content";
        return {
          ok: true,
          json: async () => ({
            diff: `diff --git a/f.txt b/f.txt\n--- a/f.txt\n+++ b/f.txt\n@@ -0,0 +1 @@\n+${content}\n`,
          }),
        };
      }
      return fallback(url, init);
    });
    const user = userEvent.setup();
    renderDetail();

    expect(await screen.findByText("+current content")).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(([url]) => url === "/api/tasks/7/diff?base=1&untracked=1")
    ).toBe(true);
    fetchMock.mockClear();
    await user.click(screen.getByRole("button", { name: /^#1/ }));
    expect(await screen.findByText("+historical content")).toBeInTheDocument();
    expect(screen.queryByText("+current content")).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => url === "/api/tasks/7/runs/1/diff")).toBe(true);
    expect(fetchMock.mock.calls.some(([url]) => url.includes("/diff?"))).toBe(false);

    await user.click(screen.getByRole("button", { name: /^#2/ }));
    expect(await screen.findByText("+current content")).toBeInTheDocument();
    expect(screen.queryByText("+historical content")).not.toBeInTheDocument();
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

  it("surfaces a publish error inline instead of swallowing it", async () => {
    const needsApproval = {
      ...TASK,
      status: "needs_approval",
      run: { ...RUN, status: "needs_approval" },
    };
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST" && url.includes("/publish")) {
        return { ok: false, json: async () => ({ error: "PR create failed" }) };
      }
      if (url.endsWith("/runs")) {
        return { ok: true, json: async () => [needsApproval.run] };
      }
      if (url.includes("/api/tasks")) {
        return { ok: true, json: async () => needsApproval };
      }
      if (url.includes("/api/github/tokens")) {
        return { ok: true, json: async () => ({ accounts: [] }) };
      }
      if (url.includes("/api/models")) {
        return { ok: true, json: async () => ({ cli: "opencode", models: ["m1"] }) };
      }
      return { ok: true, json: async () => REPOS };
    });
    vi.stubGlobal("fetch", fetchMock);

    renderDetail();
    const publish = await screen.findByRole("button", { name: "Publish" });
    await userEvent.click(publish);

    // The publish action now goes through a confirmation dialog so the owner
    // sees the target (PR #, branch) before the push.
    const confirm = await screen.findByRole("button", { name: "Confirm" });
    await userEvent.click(confirm);

    expect(await screen.findByText("PR create failed")).toBeInTheDocument();
    // The failure is shown inline; the page is NOT replaced by a full-page error.
    expect(screen.getByText("fix the bug")).toBeInTheDocument();
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
      followups: [
        { id: 1, body: "do more", pat_name: null, model: null, created_at: "2026-08-06T10:02:00" },
      ],
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
        return { ok: true, json: async () => ({ accounts: [] }) };
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
    await userEvent.type(screen.getByPlaceholderText(/Add a regression test/), "do more");
    await userEvent.click(screen.getByRole("button", { name: "Send follow-up" }));

    await waitFor(() => expect(screen.getByText("running")).toBeInTheDocument(), { timeout: 5000 });
  });

  it("shows assigned reviewers with status and can assign more", async () => {
    const reviewerTask = {
      ...TASK,
      pr_number: 9,
      prs: [9],
      status: "done",
      run: { ...RUN, status: "done" },
      reviewers: [
        {
          id: 1,
          task_id: 8,
          agent_id: "auditor-a",
          agent_name: "Auditor A",
          run_id: 2,
          pr_number: 9,
          repo_id: 1,
          status: "posted",
          created_at: "2026-08-08T10:00:00",
        },
      ],
    };
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/runs")) {
        return { ok: true, json: async () => [reviewerTask.run] };
      }
      if (url.includes("/api/tasks") && init?.method === "POST") {
        return { ok: true, json: async () => [] };
      }
      if (url.includes("/api/tasks")) {
        return { ok: true, json: async () => reviewerTask };
      }
      if (url.includes("/api/agents")) {
        return {
          ok: true,
          json: async () => [
            { id: "auditor-a", name: "Auditor A", kind: "reviewer", skills: [], enabled: true },
            { id: "auditor-b", name: "Auditor B", kind: "reviewer", skills: [], enabled: true },
          ],
        };
      }
      if (url.includes("/api/github/tokens")) {
        return { ok: true, json: async () => ({ accounts: [] }) };
      }
      if (url.includes("/api/models")) {
        return { ok: true, json: async () => ({ cli: "opencode", models: ["m1"] }) };
      }
      return { ok: true, json: async () => REPOS };
    });
    vi.stubGlobal("fetch", fetchMock);

    renderDetail();
    expect(await screen.findByText("Reviewers")).toBeInTheDocument();
    expect(screen.getByText("Auditor A")).toBeInTheDocument();
    expect(screen.getByText("posted")).toBeInTheDocument();
    expect(screen.getByText(/1\/1 posted/)).toBeInTheDocument();

    // Auditor A is already assigned, so only Auditor B is offered.
    await userEvent.click(screen.getByRole("button", { name: /Auditor B/ }));
    await waitFor(() => {
      const assignCall = fetchMock.mock.calls.find(
        ([url, init]) => String(url).includes("/api/tasks/7/reviewers") && init?.method === "POST"
      );
      expect(assignCall).toBeDefined();
      const body = JSON.parse((assignCall?.[1] as RequestInit).body as string) as {
        reviewers: string[];
      };
      expect(body.reviewers).toEqual(["auditor-b"]);
    });
  });

  it("hides Address reviewers on a pr_review task (fixer-only action)", async () => {
    const reviewTask = {
      ...TASK,
      type: "pr_review",
      pr_number: 9,
      prs: [9],
      status: "done",
      run: { ...RUN, status: "done" },
      reviewers: [],
    };
    stubFetch(reviewTask);
    vi.stubGlobal("EventSource", FakeEventSource);

    renderDetail();
    await screen.findByText("Follow-up");
    // The follow-up composer exists, but the fixer-only Address-reviewers
    // button must NOT be offered on a reviewer task.
    expect(screen.queryByRole("button", { name: "Address reviewers" })).toBeNull();
  });

  it("shows Address reviewers on a fix task that has a PR", async () => {
    const fixTask = {
      ...TASK,
      type: "issue_fix",
      pr_number: 9,
      prs: [9],
      status: "done",
      run: { ...RUN, status: "done" },
      reviewers: [],
    };
    stubFetch(fixTask);
    vi.stubGlobal("EventSource", FakeEventSource);

    renderDetail();
    await screen.findByText("Follow-up");
    expect(await screen.findByRole("button", { name: "Address reviewers" })).toBeInTheDocument();
  });

  it("Address reviewers opens the New-task form prefilled (no follow-up POST)", async () => {
    const fixTask = {
      ...TASK,
      type: "issue_fix",
      repo_id: 1,
      pr_number: 9,
      prs: [9],
      status: "done",
      run: { ...RUN, status: "done" },
      reviewers: [],
    };
    const fetchMock = stubFetch(fixTask);
    vi.stubGlobal("EventSource", FakeEventSource);

    const captured: {
      current: { pathname: string; search: string; state: unknown } | null;
    } = { current: null };
    function Probe() {
      const loc = useLocation();
      captured.current = { pathname: loc.pathname, search: loc.search, state: loc.state };
      return null;
    }
    render(
      <MemoryRouter initialEntries={["/tasks/7"]}>
        <Routes>
          <Route path="/tasks/:id" element={<TaskDetail />} />
          <Route path="/" element={<Probe />} />
        </Routes>
      </MemoryRouter>
    );
    await screen.findByText("Follow-up");
    await userEvent.click(await screen.findByRole("button", { name: "Address reviewers" }));

    await waitFor(() => expect(captured.current?.pathname).toBe("/"));
    expect(captured.current?.search).toBe("?view=queue");
    const state = captured.current?.state as {
      prefill: Record<string, unknown>;
      from: string;
    };
    expect(state.from).toBe("task-detail");
    expect(state.prefill).toMatchObject({
      repoId: 1,
      type: "freeform",
      prNumber: "9",
      addressReviews: true,
    });
    expect(String(state.prefill.prompt)).toContain("PR #9");
    // Handoff only — nothing is posted until the user submits the new form.
    expect(fetchMock.mock.calls.filter((c) => c[1]?.method === "POST")).toEqual([]);
  });

  it("shows the waiting card for a waiting_input run", async () => {
    const waitingTask = {
      ...TASK,
      status: "done",
      run: {
        ...RUN,
        status: "done",
        waiting_input: true,
        finished_at: "2026-08-06T10:02:00",
        steps: [
          {
            type: "message",
            text: "# H\n\nPlan. **waiting for explicit approval**.",
            ts: "2026-08-06T10:01:05",
          },
        ],
      },
    };
    stubFetch(waitingTask);
    vi.stubGlobal("EventSource", FakeEventSource);

    renderDetail();
    expect(await screen.findByText("Agent is waiting for your input")).toBeInTheDocument();
    // The message is rendered as markdown in the card AND the timeline.
    expect(screen.getAllByText(/Plan/).length).toBeGreaterThan(0);
  });

  it("does not show the waiting card when waiting_input is false", async () => {
    const normalTask = {
      ...TASK,
      status: "done",
      run: {
        ...RUN,
        status: "done",
        waiting_input: false,
        steps: [{ type: "message", text: "All done.", ts: "2026-08-06T10:01:05" }],
      },
    };
    stubFetch(normalTask);
    vi.stubGlobal("EventSource", FakeEventSource);

    renderDetail();
    await screen.findByText("Follow-up");
    expect(screen.queryByText("Agent is waiting for your input")).toBeNull();
  });

  it("Reply in follow-up prefills the composer with quoted context", async () => {
    const waitingTask = {
      ...TASK,
      status: "done",
      run: {
        ...RUN,
        status: "done",
        waiting_input: true,
        finished_at: "2026-08-06T10:02:00",
        steps: [
          {
            type: "message",
            text: "# H\n\nPlan. **waiting for explicit approval**.",
            ts: "2026-08-06T10:01:05",
          },
        ],
      },
    };
    stubFetch(waitingTask);
    vi.stubGlobal("EventSource", FakeEventSource);

    renderDetail();
    await screen.findByText("Agent is waiting for your input");
    await userEvent.click(screen.getByRole("button", { name: "Reply in follow-up" }));

    const textarea = screen.getByPlaceholderText(/Add a regression test/) as HTMLTextAreaElement;
    expect(textarea.value.startsWith("> ")).toBe(true);
  });

  describe("publish modes", () => {
    function doneTask(overrides: Record<string, unknown> = {}) {
      return {
        ...TASK,
        ...overrides,
        status: "done",
        run: { ...RUN, status: "done" },
      };
    }

    function stubFetchWithPublish(task: Record<string, unknown>, publishResponse: unknown) {
      const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
        if (init?.method === "POST" && url.includes("/publish")) {
          return { ok: true, json: async () => publishResponse };
        }
        if (url.endsWith("/runs")) {
          return { ok: true, json: async () => [task.run ?? RUN] };
        }
        if (url.includes("/api/tasks")) {
          return { ok: true, json: async () => task };
        }
        if (url.includes("/api/github/tokens")) {
          return { ok: true, json: async () => ({ accounts: [] }) };
        }
        if (url.includes("/api/models")) {
          return { ok: true, json: async () => ({ cli: "opencode", models: ["m1"] }) };
        }
        return { ok: true, json: async () => REPOS };
      });
      vi.stubGlobal("fetch", fetchMock);
      return fetchMock;
    }

    it("shows 'Publish' when no PR is linked", async () => {
      stubFetchWithPublish(doneTask({ prs: [] }), {
        status: "done",
        mode: "new_pr",
        pr_number: 42,
      });
      renderDetail();
      const btn = await screen.findByRole("button", { name: "Publish" });
      await userEvent.click(btn);
      const confirm = await screen.findByRole("button", { name: "Confirm" });
      await userEvent.click(confirm);
      await waitFor(() => {
        const call = vi
          .mocked(fetch)
          .mock.calls.find(
            ([url, init]) => String(url).includes("/api/tasks/7/publish") && init?.method === "POST"
          );
        expect(call).toBeDefined();
        const body = JSON.parse((call?.[1] as RequestInit).body as string) as {
          mode?: string;
        };
        // Default mode is new_pr.
        expect(body.mode).toBe("new_pr");
      });
    });

    it("defaults to 'Push to PR #N' when a PR was attached at creation", async () => {
      stubFetchWithPublish(doneTask({ prs: [9] }), {
        status: "done",
        mode: "update_pr",
        pr_number: 9,
      });
      renderDetail();
      const btn = await screen.findByRole("button", { name: "Push to PR #9" });
      await userEvent.click(btn);
      const confirm = await screen.findByRole("button", { name: "Confirm" });
      await userEvent.click(confirm);
      await waitFor(() => {
        const call = vi
          .mocked(fetch)
          .mock.calls.find(
            ([url, init]) => String(url).includes("/api/tasks/7/publish") && init?.method === "POST"
          );
        expect(call).toBeDefined();
        const body = JSON.parse((call?.[1] as RequestInit).body as string) as {
          mode?: string;
          pr_number?: number;
        };
        expect(body.mode).toBe("update_pr");
        expect(body.pr_number).toBe(9);
      });
    });

    it("Advanced disclosure exposes push_branch with a branch input", async () => {
      stubFetchWithPublish(doneTask({ prs: [] }), {
        status: "done",
        mode: "push_branch",
        branch: "feature/manual",
      });
      renderDetail();
      await screen.findByRole("button", { name: "Publish" });
      await userEvent.click(screen.getByRole("button", { name: "Advanced" }));
      // Pick the "Push to branch" radio.
      const radios = screen.getAllByRole("radio", { name: /Push to specific branch/ });
      await userEvent.click(radios[0]);
      // Fill the branch input.
      const branchInput = screen.getByPlaceholderText("branch name");
      await userEvent.type(branchInput, "feature/manual");
      // Click "Run" inside the Advanced panel.
      await userEvent.click(screen.getByRole("button", { name: "Run" }));
      const confirm = await screen.findByRole("button", { name: "Confirm" });
      await userEvent.click(confirm);
      await waitFor(() => {
        const call = vi
          .mocked(fetch)
          .mock.calls.find(
            ([url, init]) => String(url).includes("/api/tasks/7/publish") && init?.method === "POST"
          );
        expect(call).toBeDefined();
        const body = JSON.parse((call?.[1] as RequestInit).body as string) as {
          mode?: string;
          branch?: string;
        };
        expect(body.mode).toBe("push_branch");
        expect(body.branch).toBe("feature/manual");
      });
    });

    it("surfaces a server error from publishTask inside the dialog", async () => {
      const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
        if (init?.method === "POST" && url.includes("/publish")) {
          return { ok: false, status: 409, json: async () => ({ error: "conflict" }) };
        }
        if (url.endsWith("/runs")) {
          return { ok: true, json: async () => [RUN] };
        }
        if (url.includes("/api/tasks")) {
          return { ok: true, json: async () => doneTask() };
        }
        if (url.includes("/api/github/tokens")) {
          return { ok: true, json: async () => ({ accounts: [] }) };
        }
        if (url.includes("/api/models")) {
          return { ok: true, json: async () => ({ cli: "opencode", models: ["m1"] }) };
        }
        return { ok: true, json: async () => REPOS };
      });
      vi.stubGlobal("fetch", fetchMock);
      renderDetail();
      const publish = await screen.findByRole("button", { name: "Publish" });
      await userEvent.click(publish);
      const confirm = await screen.findByRole("button", { name: "Confirm" });
      await userEvent.click(confirm);
      expect(await screen.findByText("conflict")).toBeInTheDocument();
    });

    it("lists the repo's open PRs in the update_pr picker and can publish to a picked PR", async () => {
      const task = doneTask({
        prs: [],
        source_branch: "phase-1",
        target_branch: "main",
        pat_name: "RB",
        repo_full_name: "owner/repo",
      });
      const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
        if (init?.method === "POST" && url.includes("/publish")) {
          return {
            ok: true,
            json: async () => ({ status: "done", mode: "update_pr", pr_number: 5 }),
          };
        }
        if (url.endsWith("/runs")) return { ok: true, json: async () => [task.run ?? RUN] };
        if (url.includes("/api/tasks")) return { ok: true, json: async () => task };
        if (url.includes("/api/github/tokens"))
          return { ok: true, json: async () => ({ accounts: [] }) };
        if (url.includes("/api/models"))
          return { ok: true, json: async () => ({ cli: "opencode", models: ["m1"] }) };
        if (url.includes("/api/github/context")) {
          return {
            ok: true,
            json: async () => ({
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
                {
                  number: 5,
                  title: "Housekeeping",
                  html_url: "u",
                  state: "open",
                  base: "main",
                  head: "chore",
                  author: "me",
                },
              ],
              branches: ["main", "phase-1"],
            }),
          };
        }
        return { ok: true, json: async () => REPOS };
      });
      vi.stubGlobal("fetch", fetchMock);

      renderDetail();
      // No linked PR, but PR #1's head (phase-1) matches the task's source
      // branch → the smart default targets it instead of opening a new PR.
      await waitFor(() => {
        expect(screen.getByRole("button", { name: "Push to PR #1" })).toBeInTheDocument();
      });

      // Advanced → Update existing PR → the picker shows the repo's open PRs.
      await userEvent.click(screen.getByRole("button", { name: "Advanced" }));
      await userEvent.click(screen.getByRole("radio", { name: /Update existing PR/ }));
      const select = screen.getByRole("combobox", { name: "Pull request to update" });
      expect(screen.getByRole("option", { name: /#1 — Phase 1/ })).toBeInTheDocument();
      expect(screen.getByRole("option", { name: /#5 — Housekeeping/ })).toBeInTheDocument();

      // Pick a different open PR and run the publish.
      await userEvent.selectOptions(select, "5");
      await userEvent.click(screen.getByRole("button", { name: "Run" }));
      const confirm = await screen.findByRole("button", { name: "Confirm" });
      await userEvent.click(confirm);
      await waitFor(() => {
        const call = vi
          .mocked(fetch)
          .mock.calls.find(
            ([url, init]) => String(url).includes("/api/tasks/7/publish") && init?.method === "POST"
          );
        expect(call).toBeDefined();
        const body = JSON.parse((call?.[1] as RequestInit).body as string) as {
          mode?: string;
          pr_number?: number;
        };
        expect(body.mode).toBe("update_pr");
        expect(body.pr_number).toBe(5);
      });
    });

    it("falls back to linked PRs when the repo's open PRs cannot be loaded", async () => {
      const task = doneTask({ prs: [9], pat_name: "RB" });
      const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
        if (init?.method === "POST" && url.includes("/publish")) {
          return {
            ok: true,
            json: async () => ({ status: "done", mode: "update_pr", pr_number: 9 }),
          };
        }
        if (url.endsWith("/runs")) return { ok: true, json: async () => [task.run ?? RUN] };
        if (url.includes("/api/tasks")) return { ok: true, json: async () => task };
        if (url.includes("/api/github/tokens"))
          return { ok: true, json: async () => ({ accounts: [] }) };
        if (url.includes("/api/models"))
          return { ok: true, json: async () => ({ cli: "opencode", models: ["m1"] }) };
        if (url.includes("/api/github/context")) {
          return { ok: false, status: 502, json: async () => ({ error: "no token" }) };
        }
        return { ok: true, json: async () => REPOS };
      });
      vi.stubGlobal("fetch", fetchMock);

      renderDetail();
      await screen.findByRole("button", { name: "Push to PR #9" });
      await userEvent.click(screen.getByRole("button", { name: "Advanced" }));
      await userEvent.click(screen.getByRole("radio", { name: /Update existing PR/ }));
      const select = screen.getByRole("combobox", { name: "Pull request to update" });
      expect(screen.getByRole("option", { name: "PR #9" })).toBeInTheDocument();
      expect(select).toHaveTextContent(/couldn't load PRs/);

      await userEvent.click(screen.getByRole("button", { name: "Run" }));
      const confirm = await screen.findByRole("button", { name: "Confirm" });
      await userEvent.click(confirm);
      await waitFor(() => {
        const call = vi
          .mocked(fetch)
          .mock.calls.find(
            ([url, init]) => String(url).includes("/api/tasks/7/publish") && init?.method === "POST"
          );
        expect(call).toBeDefined();
        const body = JSON.parse((call?.[1] as RequestInit).body as string) as {
          mode?: string;
          pr_number?: number;
        };
        expect(body.mode).toBe("update_pr");
        expect(body.pr_number).toBe(9);
      });
    });
  });
});

// ---- Phase 4 T3.2 — merge-readiness panel ------------------------------

describe("TaskDetail (Phase 4 T3.2)", () => {
  function stubFetchWithPublishCheck(
    task: Record<string, unknown>,
    settings: Record<string, unknown> | null,
    publishCheck: Record<string, unknown> | null
  ) {
    const fetchMock = vi.fn(async (url: string, _init?: RequestInit) => {
      if (url.endsWith("/runs")) {
        return { ok: true, json: async () => [task.run ?? RUN] };
      }
      if (url.includes("/publish-check")) {
        if (publishCheck) return { ok: true, json: async () => publishCheck };
        return { ok: true, json: async () => ({ status: "ready", base_ref: "main", checks: [] }) };
      }
      if (url.includes("/api/tasks")) {
        return { ok: true, json: async () => task };
      }
      if (url.includes("/api/settings")) {
        if (settings) return { ok: true, json: async () => settings };
        return {
          ok: true,
          json: async () => ({ default_backend: "opencode", default_model: "m1" }),
        };
      }
      if (url.includes("/api/github/tokens")) {
        return { ok: true, json: async () => ({ accounts: [] }) };
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

  it("shows the merge-readiness panel for a publishable task (ready)", async () => {
    const done = {
      ...TASK,
      status: "done",
      run: { ...RUN, status: "done", has_diff: true },
      attention: "ready_to_merge",
    };
    stubFetchWithPublishCheck(done, null, {
      status: "ready",
      base_ref: "main",
      checks: [
        { name: "branch", ok: true, message: "on jalebi branch" },
        { name: "commits", ok: true, ahead: 2, message: "2 commits ahead of origin/main" },
        { name: "conflict", ok: true, conflicts: [], message: "no predicted conflicts" },
        { name: "ci", ok: true, state: "success", message: "CI is green" },
        { name: "review", ok: true, decision: "approved", message: "PR is approved" },
        { name: "mergeable", ok: true, mergeable: true, message: "PR is mergeable" },
      ],
    });
    renderDetail();
    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(screen.getByText("CI is green")).toBeInTheDocument();
    expect(screen.getByText("PR is approved")).toBeInTheDocument();
  });

  it("reflects a blocked state (branch mismatch)", async () => {
    const done = {
      ...TASK,
      status: "done",
      run: { ...RUN, status: "done", has_diff: true },
      attention: "needs_you",
    };
    stubFetchWithPublishCheck(done, null, {
      status: "blocked",
      base_ref: "main",
      checks: [
        {
          name: "branch",
          ok: false,
          message: "branch mismatch; agent left HEAD on main, expected jalebi/7",
        },
        { name: "commits", ok: true, ahead: 1, message: "1 commit ahead of origin/main" },
      ],
    });
    renderDetail();
    expect(await screen.findByText("Blocked")).toBeInTheDocument();
    expect(
      screen.getByText(/branch mismatch; agent left HEAD on main, expected jalebi\/7/i)
    ).toBeInTheDocument();
  });

  it("shows dependency badges in the header for a blocked task", async () => {
    const blocked = {
      ...TASK,
      status: "blocked",
      depends_on: [3],
      blocked_by: [3],
      blocking: [],
      blocked: true,
    };
    stubFetchWithPublishCheck(blocked, null, null);
    renderDetail();
    expect(await screen.findByText("⛔ blocked")).toBeInTheDocument();
    expect(screen.getByText("depends on #3")).toBeInTheDocument();
  });

  it("places the Files panel above the Diff panel", async () => {
    const withDiff = {
      ...TASK,
      status: "done",
      run: { ...RUN, status: "done", has_diff: true },
    };
    stubFetchWithPublishCheck(withDiff, null, null);
    renderDetail();
    const filesH = await screen.findByText("Files");
    const diffH = await screen.findByText("Diff");
    expect(filesH.compareDocumentPosition(diffH) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

// ---- Phase 4 T6 — Open worktree ---------------------------------------

describe("TaskDetail (Phase 4 T6 — open worktree)", () => {
  it("posts /open-in-ide when ide_command is configured", async () => {
    const waitingTask = {
      ...TASK,
      status: "done",
      attention: "needs_you",
      run: {
        ...RUN,
        status: "done",
        finished_at: "2026-08-06T10:01:30",
        waiting_input: true,
        steps: [
          {
            type: "message",
            text: "# H\n\nPlan. **waiting for explicit approval**.",
            ts: "2026-08-06T10:01:00",
          },
        ],
      },
    };
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/runs")) {
        return { ok: true, json: async () => [waitingTask.run] };
      }
      if (url.includes("/open-in-ide")) {
        void init;
        return { ok: true, json: async () => ({ ok: true, path: "/tmp/x" }) };
      }
      if (url.includes("/api/settings")) {
        return {
          ok: true,
          json: async () => ({
            default_backend: "opencode",
            default_model: "m1",
            ide_command: "code",
          }),
        };
      }
      if (url.includes("/api/tasks")) {
        return { ok: true, json: async () => waitingTask };
      }
      if (url.includes("/api/github/tokens")) {
        return { ok: true, json: async () => ({ accounts: [] }) };
      }
      if (url.includes("/api/models")) {
        return { ok: true, json: async () => ({ cli: "opencode", models: ["m1"] }) };
      }
      return { ok: true, json: async () => REPOS };
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MemoryRouter initialEntries={["/tasks/7"]}>
        <Routes>
          <Route path="/tasks/:id" element={<TaskDetail />} />
        </Routes>
      </MemoryRouter>
    );
    await screen.findByText("Agent is waiting for your input");
    await userEvent.click(screen.getByText("Open worktree"));
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) =>
            String(url).includes("/api/tasks/7/open-in-ide") && init?.method === "POST"
        )
      ).toBe(true);
    });
  });

  it("renders Open in <ideName> button in header and posts to open-in-ide", async () => {
    const customTask = { ...TASK, status: "running" };
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      void init;
      if (url.endsWith("/runs")) {
        return { ok: true, json: async () => [] };
      }
      if (url.includes("/open-in-ide")) {
        return { ok: true, json: async () => ({ ok: true, path: "/tmp/worktree" }) };
      }
      if (url.includes("/api/settings")) {
        return {
          ok: true,
          json: async () => ({
            default_backend: "opencode",
            default_model: "m1",
            ide_command: "cursor",
            ide_name: "Cursor",
          }),
        };
      }
      if (url.includes("/api/tasks")) {
        return { ok: true, json: async () => customTask };
      }
      if (url.includes("/api/github/tokens")) {
        return { ok: true, json: async () => ({ accounts: [] }) };
      }
      if (url.includes("/api/models")) {
        return { ok: true, json: async () => ({ cli: "opencode", models: ["m1"] }) };
      }
      return { ok: true, json: async () => REPOS };
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MemoryRouter initialEntries={["/tasks/7"]}>
        <Routes>
          <Route path="/tasks/:id" element={<TaskDetail />} />
        </Routes>
      </MemoryRouter>
    );
    const ideBtns = await screen.findAllByText("Open in Cursor");
    expect(ideBtns.length).toBeGreaterThanOrEqual(1);
    await userEvent.click(ideBtns[0]);
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) =>
            String(url).includes("/api/tasks/7/open-in-ide") && init?.method === "POST"
        )
      ).toBe(true);
    });
  });

  it("keeps Open worktree disabled with a settings link when unconfigured", async () => {
    const waitingTask = {
      ...TASK,
      status: "done",
      attention: "needs_you",
      run: {
        ...RUN,
        status: "done",
        finished_at: "2026-08-06T10:01:30",
        waiting_input: true,
        steps: [
          {
            type: "message",
            text: "waiting for your approval.",
            ts: "2026-08-06T10:01:00",
          },
        ],
      },
    };
    const fetchMock = vi.fn(async (url: string) => {
      if (url.endsWith("/runs")) {
        return { ok: true, json: async () => [waitingTask.run] };
      }
      if (url.includes("/api/settings")) {
        return {
          ok: true,
          json: async () => ({
            default_backend: "opencode",
            default_model: "m1",
            ide_command: "",
          }),
        };
      }
      if (url.includes("/api/tasks")) {
        return { ok: true, json: async () => waitingTask };
      }
      if (url.includes("/api/github/tokens")) {
        return { ok: true, json: async () => ({ accounts: [] }) };
      }
      if (url.includes("/api/models")) {
        return { ok: true, json: async () => ({ cli: "opencode", models: ["m1"] }) };
      }
      return { ok: true, json: async () => REPOS };
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MemoryRouter initialEntries={["/tasks/7"]}>
        <Routes>
          <Route path="/tasks/:id" element={<TaskDetail />} />
        </Routes>
      </MemoryRouter>
    );
    await screen.findByText("Agent is waiting for your input");
    const btn = screen.getByText("Open worktree") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(screen.getByText("configure IDE in Settings")).toBeInTheDocument();
    expect(screen.getByText("configure IDE in Settings").closest("a")).toHaveAttribute(
      "href",
      "/settings?section=ide"
    );
  });
});

// ---- Phase 4 T7 — FileBrowser mounting ----------------------------------

describe("TaskDetail (Phase 4 T7 — file browser)", () => {
  function renderNoFilesTask(task: Record<string, unknown>) {
    const fetchMock = vi.fn(async (url: string) => {
      if (url.endsWith("/runs"))
        return { ok: true, json: async () => (task.run ? [task.run] : []) };
      if (url.includes("/files"))
        return { ok: true, json: async () => ({ path: "", entries: [] }) };
      if (url.includes("/api/tasks")) return { ok: true, json: async () => task };
      if (url.includes("/api/settings"))
        return {
          ok: true,
          json: async () => ({ default_backend: "opencode", default_model: "m1" }),
        };
      if (url.includes("/api/github/tokens"))
        return { ok: true, json: async () => ({ accounts: [] }) };
      if (url.includes("/api/models"))
        return { ok: true, json: async () => ({ cli: "opencode", models: ["m1"] }) };
      return { ok: true, json: async () => REPOS };
    });
    vi.stubGlobal("fetch", fetchMock);
    return render(
      <MemoryRouter initialEntries={["/tasks/7"]}>
        <Routes>
          <Route path="/tasks/:id" element={<TaskDetail />} />
        </Routes>
      </MemoryRouter>
    );
  }

  it("mounts FileBrowser when the task has a run", async () => {
    const doneTask = { ...TASK, status: "done", run: { ...RUN, status: "done" } };
    renderNoFilesTask(doneTask);
    expect(await screen.findByText("Files")).toBeInTheDocument();
  });

  it("does not mount FileBrowser when the task has no run", async () => {
    const noRun = { ...TASK, run: null };
    renderNoFilesTask(noRun);
    await screen.findByText("fix the bug");
    expect(screen.queryByText("Files")).toBeNull();
  });
});

describe("TaskDetail improvements", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
    FakeEventSource.instances = [];
  });

  function stubFetchPlus(task: Record<string, unknown>, extra: Record<string, unknown> = {}) {
    const fetchMock = vi.fn(async (url: string, _init?: RequestInit) => {
      if (url.includes("/api/agents/")) {
        return {
          ok: true,
          json: async () => ({
            id: "auditor",
            name: "Security Auditor",
            avatar: "shield",
          }),
        };
      }
      const match = Object.entries(extra).find(([needle]) => url.includes(needle));
      if (match) return { ok: true, json: async () => match[1] };
      if (url.endsWith("/runs")) {
        return { ok: true, json: async () => [task.run ?? RUN] };
      }
      if (url.includes("/files")) {
        return { ok: true, json: async () => ({ path: "", entries: [] }) };
      }
      if (url.includes("/api/tasks")) {
        return { ok: true, json: async () => task };
      }
      if (url.includes("/api/settings")) {
        return {
          ok: true,
          json: async () => ({ default_backend: "opencode", default_model: "m1" }),
        };
      }
      if (url.includes("/api/github/tokens")) {
        return { ok: true, json: async () => ({ accounts: [] }) };
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

  function taskFetches(fetchMock: ReturnType<typeof vi.fn>) {
    return fetchMock.mock.calls.filter(([u]) => String(u).includes("/api/tasks/7")).length;
  }

  function stubClipboard() {
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    return writeText;
  }

  it("polls while queued and offers refresh", async () => {
    const fetchMock = stubFetchPlus({ ...TASK, status: "queued", run: null });
    vi.stubGlobal("EventSource", FakeEventSource);
    const setIntervalSpy = vi.spyOn(window, "setInterval");
    renderDetail();
    await screen.findByText("fix the bug");
    expect(taskFetches(fetchMock)).toBeGreaterThanOrEqual(1);

    // Queued polling ticks every 5s — fire the captured callback manually.
    const poll = setIntervalSpy.mock.calls.find((c) => c[1] === 5000)?.[0] as
      (() => void) | undefined;
    expect(poll).toBeDefined();
    await act(async () => {
      poll!();
    });
    expect(taskFetches(fetchMock)).toBeGreaterThanOrEqual(2);

    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(taskFetches(fetchMock)).toBeGreaterThanOrEqual(3);
  });

  it("shows the agent chip with avatar and live elapsed/timeout", async () => {
    stubFetchPlus({ ...TASK, agent_id: "auditor" });
    vi.stubGlobal("EventSource", FakeEventSource);
    renderDetail();

    expect(await screen.findByText("Security Auditor")).toBeInTheDocument();
    expect(screen.getByTitle("Agent: Security Auditor")).toBeInTheDocument();
    const img = screen.getByTitle("Agent: Security Auditor").querySelector("img");
    expect(img).toHaveAttribute("src", "/avatars/shield.svg");
    // Vitality: elapsed / timeout while running.
    expect(screen.getByText(/\/ 30m/)).toBeInTheDocument();
  });

  it("renders the run strip for a single run", async () => {
    stubFetchPlus(TASK);
    vi.stubGlobal("EventSource", FakeEventSource);
    renderDetail();
    await screen.findByText("fix the bug");

    expect(screen.getByRole("heading", { name: "Run" })).toBeInTheDocument();
    expect(screen.getByText("#1")).toBeInTheDocument();
  });

  it("filters timeline steps by text and type", async () => {
    stubFetchPlus(TASK);
    vi.stubGlobal("EventSource", FakeEventSource);
    renderDetail();
    // The step renders in both Timeline and Console — wait for both.
    await screen.findAllByText("scanning repo");

    await userEvent.type(screen.getByPlaceholderText("Filter steps…"), "nothing-matches");
    expect(await screen.findByText("No steps match the current filter.")).toBeInTheDocument();

    await userEvent.clear(screen.getByPlaceholderText("Filter steps…"));
    await userEvent.click(screen.getByRole("button", { name: "error" }));
    // The message step leaves the Timeline (it stays in the Console pane).
    const timeline = document.querySelector("ol.space-y-3");
    expect(timeline?.textContent ?? "").not.toContain("scanning repo");
    expect(screen.getByText(/0\/1/)).toBeInTheDocument();
  });

  it("hints that follow-ups open when the run finishes", async () => {
    stubFetchPlus(TASK);
    vi.stubGlobal("EventSource", FakeEventSource);
    renderDetail();
    await screen.findByText("fix the bug");

    expect(screen.queryByText("Follow-up")).not.toBeInTheDocument();
    expect(screen.getByText("Follow-ups open when this run finishes.")).toBeInTheDocument();
  });

  it("copies the prompt to the clipboard", async () => {
    const writeText = stubClipboard();
    stubFetchPlus(TASK);
    vi.stubGlobal("EventSource", FakeEventSource);
    renderDetail();
    await screen.findByText("fix the bug");

    await userEvent.click(screen.getByLabelText("Copy prompt"));
    expect(writeText).toHaveBeenCalledWith("fix the bug");
    expect(await screen.findByLabelText("Copy prompt")).toHaveTextContent("copied ✓");
  });

  it("copies the waiting message from the WaitingCard", async () => {
    const writeText = stubClipboard();
    stubFetchPlus({
      ...TASK,
      status: "needs_approval",
      run: { ...RUN, status: "needs_approval", waiting_input: true },
    });
    vi.stubGlobal("EventSource", FakeEventSource);
    renderDetail();

    expect(await screen.findByText("Agent is waiting for your input")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Copy message" }));
    expect(writeText).toHaveBeenCalledWith("scanning repo");
  });

  it("resubscribes and reloads when the tab becomes visible again", async () => {
    const fetchMock = stubFetchPlus(TASK);
    vi.stubGlobal("EventSource", FakeEventSource);
    renderDetail();
    await screen.findByText("fix the bug");
    await waitFor(() => expect(FakeEventSource.instances.length).toBeGreaterThan(0));
    const before = taskFetches(fetchMock);

    Object.defineProperty(document, "visibilityState", {
      value: "visible",
      configurable: true,
    });
    document.dispatchEvent(new Event("visibilitychange"));

    await waitFor(() => expect(taskFetches(fetchMock)).toBeGreaterThan(before));
    await waitFor(() => expect(FakeEventSource.instances.length).toBeGreaterThan(1));
  });

  it("renders the publish dialog into document.body (viewport-centered)", async () => {
    stubFetchPlus({
      ...TASK,
      status: "needs_approval",
      run: { ...RUN, status: "needs_approval" },
    });
    vi.stubGlobal("EventSource", FakeEventSource);
    renderDetail();
    await screen.findByText("fix the bug");

    await userEvent.click(screen.getByRole("button", { name: "Publish" }));
    const dialog = await screen.findByRole("dialog");
    expect(document.body.contains(dialog)).toBe(true);
    // Portaled out of the transformed TaskDetail tree (and <main>), so
    // `fixed inset-0` centers in the viewport instead of the tall page.
    expect(document.querySelector("main [role='dialog']")).toBeNull();
  });

  it("ignores ping heartbeats without touching the timeline", async () => {
    stubFetchPlus(TASK);
    vi.stubGlobal("EventSource", FakeEventSource);
    renderDetail();
    await screen.findByText("fix the bug");
    await waitFor(() => expect(FakeEventSource.instances.length).toBeGreaterThan(0));

    const source = FakeEventSource.instances[FakeEventSource.instances.length - 1];
    const before = document.querySelector("ol.space-y-3")?.textContent;
    source.emit({ type: "connected" });
    source.emit({ type: "ping" });
    expect(document.querySelector("ol.space-y-3")?.textContent).toBe(before);
  });

  it("resubscribes with a fresh watermark after a fatal SSE error", async () => {
    stubFetchPlus(TASK);
    (FakeEventSource as unknown as { CLOSED?: number }).CLOSED = 2;
    vi.stubGlobal("EventSource", FakeEventSource);
    renderDetail();
    await screen.findByText("fix the bug");
    await waitFor(() => expect(FakeEventSource.instances.length).toBeGreaterThan(0));
    expect(FakeEventSource.instances.length).toBe(1);

    const source = FakeEventSource.instances[0] as unknown as {
      readyState: number;
      onerror: (() => void) | null;
    };
    source.readyState = 2; // EventSource.CLOSED — the browser gave up retrying
    vi.useFakeTimers();
    try {
      act(() => {
        source.onerror?.();
      });
      await act(async () => {
        vi.advanceTimersByTime(2000);
      });
      expect(FakeEventSource.instances.length).toBe(2);
      expect(FakeEventSource.instances[1].url).toContain("after_seq=0");
    } finally {
      vi.useRealTimers();
    }
  });

  it("renders '← Back to Mission control' when arriving with from: mission", async () => {
    stubFetchPlus(TASK);
    vi.stubGlobal("EventSource", FakeEventSource);
    render(
      <MemoryRouter initialEntries={[{ pathname: "/tasks/7", state: { from: "mission" } }]}>
        <Routes>
          <Route path="/tasks/:id" element={<TaskDetail />} />
        </Routes>
      </MemoryRouter>
    );
    expect(
      await screen.findByRole("link", { name: /Back to Mission control/i })
    ).toBeInTheDocument();
  });
});
