import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

const AGENTS = [
  { id: "auditor", name: "Auditor", kind: "reviewer", skills: [], enabled: true },
];

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

function makeFetchMock() {
  return vi.fn(async (url: string, init?: RequestInit) => {
    if (String(url).includes("/api/triggers") && init?.method === "POST") {
      const body = JSON.parse(init.body as string) as Record<string, unknown>;
      return { ok: true, json: async () => ({ id: 9, ...body }) };
    }
    if (String(url).includes("/api/triggers") && init?.method === "PUT") {
      return { ok: true, json: async () => RULES[0] };
    }
    if (String(url).includes("/api/triggers")) {
      return { ok: true, json: async () => RULES };
    }
    if (String(url).includes("/api/webhooks/deliveries")) {
      return { ok: true, json: async () => [] };
    }
    if (String(url).includes("/api/webhook/status")) {
      return {
        ok: true,
        json: async () => ({
          url: "https://t.example.com",
          secret_set: true,
          reachable: true,
          repos: [{ id: 1, full_name: "owner/repo", webhook_registered: false, poll_fallback: false }],
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

describe("Triggers", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows webhook status, rules, and delivery log", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    render(<Triggers />);

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

    render(<Triggers />);
    await screen.findByText("Webhook status");

    await userEvent.click(screen.getByRole("button", { name: "+ New rule" }));
    await userEvent.type(screen.getByPlaceholderText("main"), "dev");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      const createCall = fetchMock.mock.calls.find(
        ([url, init]) => String(url).includes("/api/triggers") && init?.method === "POST"
      );
      expect(createCall).toBeDefined();
      const body = JSON.parse((createCall?.[1] as RequestInit).body as string) as {
        branch_filter: string;
      };
      expect(body.branch_filter).toBe("dev");
    });
  });

  it("registers a webhook for a repo", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    render(<Triggers />);
    await screen.findByText("Webhook status");

    await userEvent.click(screen.getByRole("button", { name: "Register" }));
    await waitFor(() => {
      const registerCall = fetchMock.mock.calls.find(
        ([url, init]) => String(url).includes("/api/repos/1/webhook") && init?.method === "POST"
      );
      expect(registerCall).toBeDefined();
    });
  });
});
