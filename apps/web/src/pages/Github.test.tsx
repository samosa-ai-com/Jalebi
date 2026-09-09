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
    localStorage.clear();
  });

  async function expandAccount(login: string) {
    // findBy: accounts load asynchronously after render.
    const btn = await screen.findByRole("button", {
      name: new RegExp(`Expand account ${login}`),
    });
    await userEvent.click(btn);
  }

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
      accounts: [ACCOUNT("primary", "acct1"), ACCOUNT("work", "acct2")],
    };
    const repos = [
      {
        full_name: "acct1/hello",
        private: false,
        default_branch: "main",
        html_url: "h1",
        account: "primary",
      },
      {
        full_name: "acct2/other",
        private: true,
        default_branch: "main",
        html_url: "h2",
        account: "work",
      },
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

    await expandAccount("acct1");
    await expandAccount("acct2");
    expect((await screen.findAllByText("acct1")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("acct2").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "acct1/hello" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "acct2/other" })).toBeInTheDocument();

    // connect a repo under the named account
    const connectButtons = screen.getAllByRole("button", { name: "Connect" });
    const acct2Connect = connectButtons.find((b) =>
      b.closest("li")?.textContent?.includes("acct2/other")
    );
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

  it("hints at the update endpoint when adding an existing account name", async () => {
    let postSeen = false;
    stubFetch({
      "/api/github/tokens": async (_url: string, init?: RequestInit) => {
        if (init?.method === "POST") {
          postSeen = true;
          return {
            ok: false,
            status: 409,
            json: async () => ({
              error:
                'account "work" already exists — use PUT /api/github/tokens/work to update its token',
            }),
          };
        }
        return { ok: true, json: async () => ({ accounts: [ACCOUNT("work", "acct2")] }) };
      },
      "/api/github/repos": [],
      "/api/repos": [],
    });

    render(
      <MemoryRouter>
        <Github />
      </MemoryRouter>
    );
    await expandAccount("acct2");
    await screen.findByRole("button", { name: "update token" });
    await userEvent.click(screen.getByRole("button", { name: "update token" }));
    await userEvent.type(screen.getByPlaceholderText("label (e.g. work, personal)"), "work");
    await userEvent.type(screen.getByPlaceholderText("ghp_…"), "ghp_new");
    await userEvent.click(screen.getByRole("button", { name: "Add account" }));

    await waitFor(() => expect(postSeen).toBe(true));
    expect(await screen.findByText(/already exists/i)).toBeInTheDocument();
  });
});

// ---- Phase 4 T5.2 — per-account repo list scrollbar -------------------

describe("Github page (token update)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  async function expandAccount(login: string) {
    // findBy: accounts load asynchronously after render.
    const btn = await screen.findByRole("button", {
      name: new RegExp(`Expand account ${login}`),
    });
    await userEvent.click(btn);
  }

  function account() {
    return ACCOUNT("work", "acct2");
  }

  it("updates an account's token via PUT", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("/api/github/tokens") && init?.method === "PUT") {
        expect(JSON.parse(init.body as string)).toEqual({ token: "ghp_new" });
        return {
          ok: true,
          json: async () => ({ updated: "work", previous_login: "acct2", login: "acct2" }),
        };
      }
      if (url.includes("/api/github/tokens")) {
        return { ok: true, json: async () => ({ accounts: [account()] }) };
      }
      return { ok: true, json: async () => [] };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <Github />
      </MemoryRouter>
    );
    await expandAccount("acct2");
    await screen.findByRole("button", { name: "update token" });
    await userEvent.click(screen.getByRole("button", { name: "update token" }));
    await userEvent.type(screen.getByPlaceholderText("new ghp_…"), "ghp_new");
    await userEvent.click(screen.getByRole("button", { name: "Update token" }));

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(
        (call) => call[0].includes("/api/github/tokens/work") && call[1]?.method === "PUT"
      );
      expect(put).toBeTruthy();
    });
  });

  it("warns when the new token belongs to a different GitHub login", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.includes("/api/github/tokens") && init?.method === "PUT") {
          return {
            ok: true,
            json: async () => ({ updated: "work", previous_login: "acct2", login: "other-user" }),
          };
        }
        if (url.includes("/api/github/tokens")) {
          return { ok: true, json: async () => ({ accounts: [account()] }) };
        }
        return { ok: true, json: async () => [] };
      })
    );

    render(
      <MemoryRouter>
        <Github />
      </MemoryRouter>
    );
    await expandAccount("acct2");
    await screen.findByRole("button", { name: "update token" });
    await userEvent.click(screen.getByRole("button", { name: "update token" }));
    await userEvent.type(screen.getByPlaceholderText("new ghp_…"), "ghp_rotated");
    await userEvent.click(screen.getByRole("button", { name: "Update token" }));

    expect(
      await screen.findByText(/now authenticates as other-user \(was acct2\)/i)
    ).toBeInTheDocument();
  });

  it("surfaces the error from a rejected update", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.includes("/api/github/tokens") && init?.method === "PUT") {
          return {
            ok: false,
            status: 400,
            json: async () => ({ stored: false, detail: { error: "Bad credentials" } }),
          };
        }
        if (url.includes("/api/github/tokens")) {
          return { ok: true, json: async () => ({ accounts: [account()] }) };
        }
        return { ok: true, json: async () => [] };
      })
    );

    render(
      <MemoryRouter>
        <Github />
      </MemoryRouter>
    );
    await expandAccount("acct2");
    await screen.findByRole("button", { name: "update token" });
    await userEvent.click(screen.getByRole("button", { name: "update token" }));
    await userEvent.type(screen.getByPlaceholderText("new ghp_…"), "ghp_bad");
    await userEvent.click(screen.getByRole("button", { name: "Update token" }));

    expect(await screen.findByText("Bad credentials")).toBeInTheDocument();
  });
});

describe("Github page (Phase 4 T5.2)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  async function expandAccount(login: string) {
    // findBy: accounts load asynchronously after render.
    const btn = await screen.findByRole("button", {
      name: new RegExp(`Expand account ${login}`),
    });
    await userEvent.click(btn);
  }

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
    await expandAccount("acct1");
    await screen.findByText("hello");
    const count = await screen.findByText("Repositories (2)");
    const wrapper = screen.getByText("hello").closest("div.rounded-md") as HTMLElement | null;
    expect(wrapper).not.toBeNull();
    expect(wrapper!.className).toContain("max-h-[26rem]");
    expect(wrapper!.className).toContain("overflow-y-auto");
    expect(wrapper!.contains(screen.getByText("hello"))).toBe(true);
    expect(wrapper!.contains(count)).toBe(false);
  });
});

describe("Github page (repo controls)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  async function expandAccount(login: string) {
    // findBy: accounts load asynchronously after render.
    const btn = await screen.findByRole("button", {
      name: new RegExp(`Expand account ${login}`),
    });
    await userEvent.click(btn);
  }

  const ACCT = ACCOUNT("primary", "acct1");
  const REPOS = [
    {
      full_name: "acct1/hello",
      private: false,
      default_branch: "main",
      html_url: "h1",
      account: "primary",
    },
    {
      full_name: "acct1/world",
      private: true,
      default_branch: "dev",
      html_url: "h2",
      account: "primary",
    },
  ];
  const CONNECTED = [{ id: 7, full_name: "acct1/world", default_branch: "dev", connected: true }];

  function renderPage(handlers: Record<string, unknown>) {
    stubFetch({
      "/api/github/tokens": { accounts: [ACCT] },
      "/api/github/repos": REPOS,
      "/api/repos": CONNECTED,
      ...handlers,
    });
    render(
      <MemoryRouter>
        <Github />
      </MemoryRouter>
    );
  }

  it("searches repos and filters by connected status", async () => {
    renderPage({});
    await expandAccount("acct1");
    await screen.findByText("hello");

    await userEvent.type(screen.getByPlaceholderText("Search repositories…"), "hello");
    expect(screen.queryByText("world")).not.toBeInTheDocument();
    expect(screen.getByText("hello")).toBeInTheDocument();

    await userEvent.clear(screen.getByPlaceholderText("Search repositories…"));
    await userEvent.click(screen.getByRole("button", { name: "Connected" }));
    expect(screen.queryByText("hello")).not.toBeInTheDocument();
    expect(screen.getByText("world")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Not connected" }));
    expect(screen.getByText("hello")).toBeInTheDocument();
    expect(screen.queryByText("world")).not.toBeInTheDocument();
  });

  it("sorts connected-first and shows filtered counts", async () => {
    renderPage({});
    await expandAccount("acct1");
    await screen.findByText("hello");
    expect(await screen.findByText("Repositories (2)")).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText("Sort repositories for primary"));
    await userEvent.type(screen.getByRole("combobox"), "connected");
    await userEvent.click(screen.getByRole("option", { name: "Sort: connected first" }));
    const items = screen.getAllByRole("listitem");
    expect(items[0].textContent).toContain("acct1/world");

    await userEvent.type(screen.getByPlaceholderText("Search repositories…"), "hello");
    expect(await screen.findByText("Repositories (1 of 2)")).toBeInTheDocument();
  });

  it("surfaces a failed account's error with a retry", async () => {
    const fetchMock = stubFetch({
      "/api/github/tokens": { accounts: [ACCT] },
      "/api/github/repos": [{ account: "primary", error: "rate limited" }],
      "/api/repos": [],
    });
    render(
      <MemoryRouter>
        <Github />
      </MemoryRouter>
    );

    await expandAccount("acct1");
    expect(await screen.findByText(/Couldn't list repositories/)).toBeInTheDocument();
    expect(screen.getByText(/rate limited/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes("account=primary"))).toBe(true);
    });
  });

  it("refresh re-fetches and connect shows a busy state", async () => {
    let resolvePost!: (v: unknown) => void;
    const fetchMock = stubFetch({
      "/api/github/tokens": { accounts: [ACCT] },
      "/api/github/repos": REPOS,
      "/api/repos": async (_url: string, init?: RequestInit) => {
        if (init?.method === "POST") {
          await new Promise((r) => {
            resolvePost = r;
          });
          return { ok: true, json: async () => ({}) };
        }
        return { ok: true, json: async () => [] };
      },
    });
    render(
      <MemoryRouter>
        <Github />
      </MemoryRouter>
    );
    await expandAccount("acct1");
    await screen.findByText("hello");

    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.filter(([u]) => String(u).includes("/api/github/tokens")).length
      ).toBeGreaterThan(1);
    });

    const connectBtns = screen.getAllByRole("button", { name: "Connect" });
    const helloConnect = connectBtns.find((b) =>
      b.closest("li")?.textContent?.includes("acct1/hello")
    );
    expect(helloConnect).toBeTruthy();
    await userEvent.click(helloConnect!);
    expect(await screen.findByRole("button", { name: "Connecting…" })).toBeDisabled();
    resolvePost({ ok: true, json: async () => ({}) });
  });

  it("explains missing scopes with a fix link", async () => {
    stubFetch({
      "/api/github/tokens": {
        accounts: [ACCOUNT("primary", "acct1", { missing_scopes: ["repo"] })],
      },
      "/api/github/repos": [],
      "/api/repos": [],
    });
    render(
      <MemoryRouter>
        <Github />
      </MemoryRouter>
    );

    await expandAccount("acct1");
    expect(await screen.findByText("Missing scopes")).toBeInTheDocument();
    expect(screen.getByText(/can't review, comment, push/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "github.com/settings/tokens" })).toHaveAttribute(
      "href",
      "https://github.com/settings/tokens"
    );
  });

  it("collapses accounts by default and toggles on header click", async () => {
    renderPage({});
    // Header is visible but the body (repos, scopes) is hidden.
    expect(await screen.findByRole("button", { name: "Expand account acct1" })).toBeInTheDocument();
    expect(screen.queryByText("hello")).not.toBeInTheDocument();
    expect(screen.queryByText("Granted scopes")).not.toBeInTheDocument();

    await expandAccount("acct1");
    expect(await screen.findByText("hello")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Collapse account acct1" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Collapse account acct1" }));
    expect(screen.queryByText("hello")).not.toBeInTheDocument();
  });

  it("expand-all and collapse-all flip every account", async () => {
    stubFetch({
      "/api/github/tokens": { accounts: [ACCT, ACCOUNT("work", "acct2")] },
      "/api/github/repos": [
        ...REPOS,
        {
          full_name: "acct2/other",
          private: false,
          default_branch: "main",
          html_url: "h3",
          account: "work",
        },
      ],
      "/api/repos": [],
    });
    render(
      <MemoryRouter>
        <Github />
      </MemoryRouter>
    );
    await screen.findByRole("button", { name: "Expand account acct1" });
    expect(screen.queryByText("hello")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Expand all" }));
    expect(await screen.findByText("hello")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "acct2/other" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Collapse all" }));
    expect(screen.queryByText("hello")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "acct2/other" })).not.toBeInTheDocument();
  });

  it("persists open accounts across remounts", async () => {
    stubFetch({
      "/api/github/tokens": { accounts: [ACCT] },
      "/api/github/repos": REPOS,
      "/api/repos": [],
    });
    const first = render(
      <MemoryRouter>
        <Github />
      </MemoryRouter>
    );
    await expandAccount("acct1");
    await screen.findByText("hello");
    first.unmount();

    // localStorage still holds the open account from the first mount.
    render(
      <MemoryRouter>
        <Github />
      </MemoryRouter>
    );
    expect(await screen.findByText("hello")).toBeInTheDocument();
  });
});
