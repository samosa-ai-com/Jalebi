import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import Repos from "./Repos";

const CONNECTED = [
  {
    id: 1,
    full_name: "owner/alpha",
    default_branch: "main",
    connected: true,
    pat_name: "work",
    webhook_registered: true,
    poll_fallback: false,
    check_runs_enabled: true,
    last_checked_at: "2026-09-07T20:00:00",
  },
  {
    id: 2,
    full_name: "owner/beta",
    default_branch: "dev",
    connected: true,
    pat_name: "personal",
    webhook_registered: false,
    poll_fallback: true,
    check_runs_enabled: false,
    last_checked_at: null,
  },
];

const DISCONNECTED = [
  {
    id: 3,
    full_name: "owner/old",
    default_branch: "main",
    connected: false,
    pat_name: "work",
    webhook_registered: false,
    poll_fallback: false,
    check_runs_enabled: false,
    last_checked_at: null,
  },
];

function makeFetchMock() {
  return vi.fn(async (url: string, init?: RequestInit) => {
    if (String(url).includes("/api/repos") && init?.method === "POST") {
      return { ok: true, json: async () => ({}) };
    }
    if (String(url).includes("/api/repos") && init?.method === "DELETE") {
      return { ok: true, json: async () => ({ disconnected: "owner/alpha" }) };
    }
    if (String(url).includes("/api/repos") && init?.method === "PATCH") {
      return { ok: true, json: async () => ({}) };
    }
    if (String(url).includes("include_disconnected=1")) {
      return { ok: true, json: async () => [...CONNECTED, ...DISCONNECTED] };
    }
    if (String(url).includes("/api/repos")) {
      return { ok: true, json: async () => CONNECTED };
    }
    return { ok: true, json: async () => [] };
  });
}

function renderPage() {
  render(
    <MemoryRouter>
      <Repos />
    </MemoryRouter>
  );
}

describe("Repos", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists connected repos with account, webhook, and check metadata", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    renderPage();

    expect(await screen.findByText("alpha")).toBeInTheDocument();
    // @work appears in the row and in the account-filter dropdown.
    expect(screen.getAllByText("@work").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("webhook: on")).toBeInTheDocument();
    expect(screen.getByText("webhook: off")).toBeInTheDocument();
    expect(screen.getByText("polling")).toBeInTheDocument();
    // "checked …" renders across two text nodes; assert via the titled spans.
    const checked = screen.getAllByTitle("Last poller check").map((el) => el.textContent ?? "");
    expect(checked.some((t) => /checked (just now|\d+[mhd] ago)/.test(t))).toBe(true);
    expect(checked).toContain("checked never");
  });

  it("has no connect form — connecting happens on the GitHub page", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    renderPage();
    await screen.findByText("alpha");

    expect(screen.queryByPlaceholderText("owner/repo")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Connect" })).not.toBeInTheDocument();
  });

  it("searches and filters by account", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    renderPage();
    await screen.findByText("alpha");

    await userEvent.type(screen.getByPlaceholderText("Search repositories…"), "beta");
    expect(screen.queryByText("alpha")).not.toBeInTheDocument();
    expect(screen.getByText("beta")).toBeInTheDocument();

    await userEvent.clear(screen.getByPlaceholderText("Search repositories…"));
    await userEvent.click(screen.getByLabelText("Filter by account"));
    await userEvent.type(screen.getByRole("combobox"), "work");
    await userEvent.click(screen.getByRole("option", { name: "@work" }));
    expect(screen.getByText("alpha")).toBeInTheDocument();
    expect(screen.queryByText("beta")).not.toBeInTheDocument();
  });

  it("toggles commit statuses with a busy state", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    renderPage();
    await screen.findByText("alpha");

    await userEvent.click(screen.getByRole("button", { name: "statuses: off" }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([url, init]) => String(url).includes("/api/repos/2") && init?.method === "PATCH"
      );
      expect(call).toBeDefined();
      expect(JSON.parse((call?.[1] as RequestInit).body as string)).toEqual({
        check_runs_enabled: true,
      });
    });
  });

  it("shows disconnected repos with reconnect", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    expect(await screen.findByText("Disconnected · 1")).toBeInTheDocument();
    expect(screen.getByText("old")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Reconnect" }));
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) => String(url).includes("/api/repos/3/reconnect") && init?.method === "POST"
        )
      ).toBe(true);
    });
  });

  it("refresh re-fetches the lists", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    renderPage();
    await screen.findByText("alpha");

    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.filter(([u]) => String(u).includes("/api/repos")).length
      ).toBeGreaterThan(2);
    });
  });

  it("empty state points at the GitHub page", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, json: async () => [] }))
    );
    renderPage();

    expect(await screen.findByRole("heading", { name: "Nothing connected yet" })).toBeInTheDocument();
    expect(screen.getByText(/Connect a repository to enable code reviews/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Connect on GitHub" })).toHaveAttribute("href", "/github");
  });
});
