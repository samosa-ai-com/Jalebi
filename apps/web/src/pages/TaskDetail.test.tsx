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

  function stubFetch(task: Record<string, unknown>) {
    const fetchMock = vi.fn(async (url: string, _init?: RequestInit) => {
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
    expect(await screen.findByText("diff --git a/f.txt b/f.txt")).toBeInTheDocument();
    expect(screen.getByText("+new")).toBeInTheDocument();
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
        ([url, init]) =>
          String(url).includes("/api/tasks/7/reviewers") && init?.method === "POST"
      );
      expect(assignCall).toBeDefined();
      const body = JSON.parse((assignCall?.[1] as RequestInit).body as string) as {
        reviewers: string[];
      };
      expect(body.reviewers).toEqual(["auditor-b"]);
    });
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
      stubFetchWithPublish(doneTask({ prs: [] }), { status: "done", mode: "new_pr", pr_number: 42 });
      renderDetail();
      const btn = await screen.findByRole("button", { name: "Publish" });
      await userEvent.click(btn);
      const confirm = await screen.findByRole("button", { name: "Confirm" });
      await userEvent.click(confirm);
      await waitFor(() => {
        const call = vi.mocked(fetch).mock.calls.find(
          ([url, init]) =>
            String(url).includes("/api/tasks/7/publish") && init?.method === "POST"
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
      stubFetchWithPublish(
        doneTask({ prs: [9] }),
        { status: "done", mode: "update_pr", pr_number: 9 }
      );
      renderDetail();
      const btn = await screen.findByRole("button", { name: "Push to PR #9" });
      await userEvent.click(btn);
      const confirm = await screen.findByRole("button", { name: "Confirm" });
      await userEvent.click(confirm);
      await waitFor(() => {
        const call = vi.mocked(fetch).mock.calls.find(
          ([url, init]) =>
            String(url).includes("/api/tasks/7/publish") && init?.method === "POST"
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
      stubFetchWithPublish(
        doneTask({ prs: [] }),
        { status: "done", mode: "push_branch", branch: "feature/manual" }
      );
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
        const call = vi.mocked(fetch).mock.calls.find(
          ([url, init]) =>
            String(url).includes("/api/tasks/7/publish") && init?.method === "POST"
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
  });
});
