import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import Github from "./Github";

const ACCOUNT = (name: string, login: string, overrides: Record<string, unknown> = {}) => ({
  name,
  login,
  masked: "ghp_****",
  token_type: "classic",
  granted_scopes: ["repo"],
  missing_scopes: [],
  note: null,
  valid: true,
  error: null,
  ...overrides,
});

const NO_TOKENS = { accounts: [] };

function stubFetch(handlers: Record<string, unknown>) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const match = Object.entries(handlers).find(([needle]) => url.includes(needle));
    if (!match) return { ok: true, json: async () => [] };
    if (match[1] instanceof Function) {
      return (match[1] as (u: string, i?: RequestInit) => Promise<unknown>)(url, init);
    }
    return { ok: true, json: async () => match[1] };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("Github", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the add-account form when no accounts are configured", async () => {
    stubFetch({ "/api/github/tokens": NO_TOKENS });
    render(
      <MemoryRouter>
        <Github />
      </MemoryRouter>
    );
    expect(await screen.findByText("Add a GitHub account")).toBeInTheDocument();
  });

  it("shows each equal account with its repos and connect uses the account name", async () => {
    const accounts = {
      accounts: [
        ACCOUNT("primary", "acct1"),
        ACCOUNT("work", "acct2"),
      ],
    };
    const repos = [
      { full_name: "acct1/hello", private: false, default_branch: "main", html_url: "h1", account: "primary" },
      { full_name: "acct2/other", private: true, default_branch: "main", html_url: "h2", account: "work" },
    ];
    const fetchMock = stubFetch({
      "/api/github/tokens": accounts,
      "/api/github/repos": repos,
      "/api/repos": [],
    });

    render(
      <MemoryRouter>
        <Github />
      </MemoryRouter>
    );

    expect((await screen.findAllByText("acct1")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("acct2").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "acct1/hello" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "acct2/other" })).toBeInTheDocument();

    // connect a repo under the named account
    const connectButtons = screen.getAllByRole("button", { name: "Connect" });
    const acct2Connect = connectButtons.find((b) => b.closest("li")?.textContent?.includes("acct2/other"));
    expect(acct2Connect).toBeTruthy();
    await userEvent.click(acct2Connect!);

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        (call) => call[0] === "/api/repos" && call[1]?.method === "POST"
      );
      expect(postCall).toBeTruthy();
      expect(JSON.parse(postCall![1]!.body as string)).toEqual({
        full_name: "acct2/other",
        pat_name: "work",
      });
    });
  });

  it("surfaces the validation error from a rejected add-account POST", async () => {
    let postSeen = false;
    stubFetch({
      "/api/github/tokens": async (_url: string, init?: RequestInit) => {
        if (init?.method === "POST") {
          postSeen = true;
          return {
            ok: false,
            status: 400,
            json: async () => ({ stored: false, detail: { error: "Bad credentials" } }),
          };
        }
        return { ok: true, json: async () => NO_TOKENS };
      },
    });

    render(
      <MemoryRouter>
        <Github />
      </MemoryRouter>
    );
    await screen.findByText("Add a GitHub account");
    await userEvent.type(screen.getByPlaceholderText("label (e.g. work, personal)"), "work");
    await userEvent.type(screen.getByPlaceholderText("ghp_…"), "ghp_bad");
    await userEvent.click(screen.getByRole("button", { name: "Add account" }));

    await waitFor(() => expect(postSeen).toBe(true));
    expect(await screen.findByText("Bad credentials")).toBeInTheDocument();
  });
});


// ---- Phase 4 T5.2 — per-account repo list scrollbar -------------------


describe("Github page (Phase 4 T5.2)", () => {
  it("bounds each account's repo list in a scrollable wrapper below the count line", async () => {
    const ACCOUNT = {
      name: "primary",
      login: "acct1",
      token_type: "classic",
      granted_scopes: ["repo"],
      missing_scopes: [],
      note: null,
      valid: true,
      error: null,
    };
    const fetchMock = vi.fn(async (url: string) => {
      if (url.includes("/api/github/tokens")) {
        return { ok: true, json: async () => ({ accounts: [ACCOUNT] }) };
      }
      if (url.includes("/api/github/repos")) {
        return {
          ok: true,
          json: async () => [
            {
              full_name: "acct1/hello",
              private: false,
              default_branch: "main",
              html_url: "https://example.invalid/h1",
              account: "primary",
            },
            {
              full_name: "acct1/world",
              private: false,
              default_branch: "main",
              html_url: "https://example.invalid/h2",
              account: "primary",
            },
          ],
        };
      }
      if (url.includes("/api/repos")) {
        return { ok: true, json: async () => [] };
      }
      return { ok: true, json: async () => [] };
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MemoryRouter>
        <Github />
      </MemoryRouter>
    );
    await screen.findByText("hello");
    const count = await screen.findByText("Repositories (2)");
    const wrapper = count.nextElementSibling as HTMLElement | null;
    expect(wrapper).not.toBeNull();
    expect(wrapper!.className).toContain("max-h-[26rem]");
    expect(wrapper!.className).toContain("overflow-y-auto");
    expect(wrapper!.contains(screen.getByText("hello"))).toBe(true);
    expect(wrapper!.contains(count)).toBe(false);
  });
});
