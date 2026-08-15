import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Repos from "./Repos";

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

function makeFetchMock() {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (url === "/api/repos" && method === "GET") {
      return { ok: true, json: async () => REPOS };
    }
    if (url === "/api/repos/1" && method === "PATCH") {
      const body = JSON.parse(String(init?.body));
      REPOS[0].check_runs_enabled = Boolean(body.check_runs_enabled);
      return { ok: true, json: async () => ({ ...REPOS[0] }) };
    }
    throw new Error(`unexpected fetch: ${method} ${url}`);
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Repos", () => {
  it("toggles commit statuses per repo", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Repos />);
    const toggle = await screen.findByRole("button", { name: "statuses: off" });
    await userEvent.click(toggle);
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        (c) => c[0] === "/api/repos/1" && c[1]?.method === "PATCH"
      );
      expect(call).toBeTruthy();
      const body = JSON.parse(String(call?.[1]?.body));
      expect(body.check_runs_enabled).toBe(true);
    });
  });
});
