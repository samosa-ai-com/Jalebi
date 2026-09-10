import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import Triggers from "./Triggers";

const REPOS = [
  {
    id: 1,
    full_name: "owner/repo",
    default_branch: "main",
    connected: true,
    pat_name: "test",
    webhook_registered: false,
    poll_fallback: false,
    check_runs_enabled: false,
    last_checked_at: null,
  },
];

const AGENTS = [{ id: "auditor", name: "Auditor", kind: "reviewer", skills: [], enabled: true }];

const RULES = [
  {
    id: 1,
    repo_id: 1,
    event: "pull_request.opened",
    action: "start_review",
    branch_filter: "main",
    label_filter: [],
    author_filter: null,
    agent_ids: ["auditor"],
    custom_instructions: null,
    enabled: true,
    created_at: "2026-08-08T00:00:00",
  },
];

const RULES_TWO = [
  RULES[0],
  {
    id: 2,
    repo_id: 1,
    event: "push",
    action: "create_task",
    branch_filter: null,
    label_filter: ["bug"],
    author_filter: "octocat",
    agent_ids: [],
    custom_instructions: "Do it.",
    enabled: false,
    created_at: "2026-08-09T00:00:00",
  },
];

const DELIVERIES = [
  {
    id: 1,
    github_delivery_id: "abc123",
    event: "pull_request",
    action: "opened",
    repo_id: 1,
    repo_full_name: "owner/repo",
    received_at: "2026-08-08T00:00:00",
    status: "matched",
    result: null,
  },
];

const DELIVERY_WITH_RESULT = {
  id: 2,
  github_delivery_id: "def456",
  event: "push",
  action: null,
  repo_id: 1,
  repo_full_name: "owner/repo",
  received_at: "2026-08-09T12:00:00",
  status: "failed",
  result: {
    rules: [
      {
        rule_id: 2,
        action: "create_task",
        work: [
          { type: "freeform", task_id: 42 },
          { type: "error", error: "boom" },
        ],
      },
    ],
  },
};

function makeFetchMock(
  deliveries: unknown[] = [],
  rules: unknown[] = RULES,
  statusUrl: string | null = "https://t.example.com",
  secretSet = true
) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    if (String(url).includes("/replay") && init?.method === "POST") {
      return { ok: true, json: async () => ({ matched: 1, results: [] }) };
    }
    if (String(url).includes("/api/triggers") && init?.method === "POST") {
      const body = JSON.parse(init.body as string) as Record<string, unknown>;
      return { ok: true, json: async () => ({ id: 9, ...body }) };
    }
    if (String(url).includes("/api/triggers") && init?.method === "PUT") {
      return { ok: true, json: async () => rules[0] };
    }
    if (String(url).includes("/api/triggers")) {
      return { ok: true, json: async () => rules };
    }
    if (String(url).includes("/api/webhooks/deliveries")) {
      return { ok: true, json: async () => deliveries };
    }
    if (String(url).includes("/api/webhook/status")) {
      return {
        ok: true,
        json: async () => ({
          url: statusUrl ?? "",
          secret_set: secretSet,
          reachable: Boolean(statusUrl),
          repos: [
            { id: 1, full_name: "owner/repo", webhook_registered: false, poll_fallback: false },
          ],
        }),
      };
    }
    if (String(url).includes("/api/agents")) {
      return { ok: true, json: async () => AGENTS };
    }
    if (String(url).includes("/api/repos")) {
      return { ok: true, json: async () => REPOS };
    }
    return { ok: true, json: async () => [] };
  });
}

function renderTriggers() {
  return render(
    <MemoryRouter>
      <Triggers />
    </MemoryRouter>
  );
}

describe("Triggers", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows webhook status, rules, and delivery log", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    renderTriggers();

    expect(await screen.findByText("Webhook status")).toBeInTheDocument();
    expect(screen.getByText("reachable")).toBeInTheDocument();
    expect(screen.getByText("pull_request.opened")).toBeInTheDocument();
    expect(screen.getByText("start_review")).toBeInTheDocument();
    expect(screen.getAllByText("owner/repo").length).toBeGreaterThan(0);
    expect(screen.getByText("Delivery log")).toBeInTheDocument();
    expect(screen.getByText("No deliveries yet.")).toBeInTheDocument();
  });

  it("creates a new trigger rule", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    renderTriggers();
    await screen.findByText("Webhook status");

    await userEvent.click(screen.getByRole("button", { name: "+ New rule" }));
    await userEvent.type(screen.getByPlaceholderText("main"), "dev");
    // start_review requires a reviewer before it will save.
    await userEvent.click(screen.getByText("Auditor (auditor)"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      const createCall = fetchMock.mock.calls.find(
        ([url, init]) => String(url).includes("/api/triggers") && init?.method === "POST"
      );
      expect(createCall).toBeDefined();
      const body = JSON.parse((createCall?.[1] as RequestInit).body as string) as {
        branch_filter: string;
        agent_ids: string[];
      };
      expect(body.branch_filter).toBe("dev");
      expect(body.agent_ids).toEqual(["auditor"]);
    });
  });

  it("refuses to save start_review with no reviewers", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    renderTriggers();
    await screen.findByText("Webhook status");

    await userEvent.click(screen.getByRole("button", { name: "+ New rule" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/needs at least one reviewer/)).toBeInTheDocument();
    const posts = fetchMock.mock.calls.filter(
      ([url, init]) => String(url).includes("/api/triggers") && init?.method === "POST"
    );
    expect(posts).toHaveLength(0);
  });

  it("shows the agent picker and requires a prompt for create_task", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    renderTriggers();
    await screen.findByText("Webhook status");

    await userEvent.click(screen.getByRole("button", { name: "+ New rule" }));
    await userEvent.click(screen.getByLabelText("Action"));
    await userEvent.type(screen.getByRole("combobox"), "create_task");
    await userEvent.click(screen.getByRole("option", { name: "create_task" }));
    expect(screen.getByText(/first selected runs the task/)).toBeInTheDocument();
    // No prompt yet → inline error, no POST.
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/needs a prompt/)).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.filter(
        ([url, init]) => String(url).includes("/api/triggers") && init?.method === "POST"
      )
    ).toHaveLength(0);
  });

  it("switching edits between rules shows the newly edited rule", async () => {
    vi.stubGlobal("fetch", makeFetchMock([], RULES_TWO));
    renderTriggers();
    await screen.findByText("Webhook status");

    const editButtons = await screen.findAllByRole("button", { name: "Edit" });
    expect(editButtons).toHaveLength(2);
    await userEvent.click(editButtons[0]);
    expect(await screen.findByRole("button", { name: "Event" })).toHaveTextContent(
      "pull_request.opened"
    );
    await userEvent.click(editButtons[1]);
    // Without the key-remount fix the form would still show rule #1's event.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Event" })).toHaveTextContent("push");
    });
  });

  it("rule rows surface author, labels, and prompt markers", async () => {
    vi.stubGlobal("fetch", makeFetchMock([], RULES_TWO));
    renderTriggers();
    await screen.findByText("Webhook status");

    expect(await screen.findByText("by octocat")).toBeInTheDocument();
    expect(screen.getByText("labels: bug")).toBeInTheDocument();
    expect(screen.getByText("has prompt")).toBeInTheDocument();
  });

  it("registers a webhook for a repo", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    renderTriggers();
    await screen.findByText("Webhook status");

    await userEvent.click(screen.getByRole("button", { name: "Register" }));
    await waitFor(() => {
      const registerCall = fetchMock.mock.calls.find(
        ([url, init]) => String(url).includes("/api/repos/1/webhook") && init?.method === "POST"
      );
      expect(registerCall).toBeDefined();
    });
  });

  it("disables Register and warns when the tunnel URL or secret is missing", async () => {
    vi.stubGlobal("fetch", makeFetchMock([], RULES, null, false));
    renderTriggers();
    await screen.findByText("Webhook status");

    expect(screen.getByText("not exposed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Register" })).toBeDisabled();
  });

  it("warns about unsigned deliveries when no secret is set", async () => {
    vi.stubGlobal("fetch", makeFetchMock([], RULES, "https://t.example.com", false));
    renderTriggers();
    await screen.findByText("Webhook status");

    expect(await screen.findByText(/unsigned/)).toBeInTheDocument();
  });

  it("expands a delivery to show per-rule work and task links", async () => {
    vi.stubGlobal("fetch", makeFetchMock([DELIVERY_WITH_RESULT]));
    renderTriggers();
    await screen.findByText("Delivery log");

    // Expand the row via its toggle button (the row header).
    const toggle = await screen.findByText("push");
    await userEvent.click(toggle);
    expect(await screen.findByText(/created tasks? 42/)).toBeInTheDocument();
    expect(screen.getByText("error: boom")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /task #42/ });
    expect(link.getAttribute("href")).toBe("/tasks/42");
  });

  it("delivery filter narrows the log", async () => {
    vi.stubGlobal("fetch", makeFetchMock([DELIVERIES[0], DELIVERY_WITH_RESULT]));
    renderTriggers();
    await screen.findByText("Delivery log");

    await userEvent.click(screen.getByLabelText("Filter by status"));
    await userEvent.type(screen.getByRole("combobox"), "matched");
    await userEvent.click(screen.getByRole("option", { name: "matched" }));
    await waitFor(() => {
      expect(screen.queryByText("push")).not.toBeInTheDocument();
    });
    expect(screen.getByText("pull_request")).toBeInTheDocument();
  });
});

it("replay button reports the outcome and refreshes the delivery log", async () => {
  const fetchMock = makeFetchMock(DELIVERIES);
  vi.stubGlobal("fetch", fetchMock);

  renderTriggers();
  await screen.findByText("Delivery log");

  await userEvent.click(screen.getByRole("button", { name: "replay" }));
  // inline outcome still appears
  expect(await screen.findByText("replayed, nothing matched")).toBeInTheDocument();

  // 1. the replay POST went out
  await waitFor(() => {
    const replayCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url).includes("/replay") && init?.method === "POST"
    );
    expect(replayCall).toBeDefined();
  });

  // 2. getDeliveries is fetched again after the replay (initial + refresh).
  // GETs come in without init.method.
  await waitFor(() => {
    const getDeliveriesCalls = fetchMock.mock.calls.filter(
      ([url, init]) => String(url).includes("/api/webhooks/deliveries") && !init?.method
    );
    expect(getDeliveriesCalls.length).toBeGreaterThanOrEqual(2);
  });
});

it("shows friendly EmptyState when there are no trigger rules", async () => {
  const fetchMock = makeFetchMock([], []);
  vi.stubGlobal("fetch", fetchMock);
  renderTriggers();
  expect(await screen.findByRole("heading", { name: "No trigger rules yet" })).toBeInTheDocument();
  expect(screen.getByText(/Create a rule to auto-start agent work/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Create rule" })).toBeInTheDocument();
});
