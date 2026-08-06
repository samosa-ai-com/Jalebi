import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import Github from "./Github";

const INVALID = {
  valid: false,
  login: null,
  token_type: null,
  granted_scopes: [],
  missing_scopes: ["repo"],
  note: null,
  error: null,
};

describe("Github", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the token form when no token is configured", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url.includes("/api/github/status")) {
        return { ok: false, status: 409, json: async () => ({ error: "no GitHub token configured" }) };
      }
      return { ok: true, json: async () => [] };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <Github />
      </MemoryRouter>
    );
    expect(await screen.findByText("Connect your GitHub account")).toBeInTheDocument();
  });

  it("shows the token form again when the stored token is invalid (revoked)", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url.includes("/api/github/status")) {
        return { ok: true, json: async () => INVALID };
      }
      return { ok: true, json: async () => [] };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <Github />
      </MemoryRouter>
    );
    expect(await screen.findByText("Connect your GitHub account")).toBeInTheDocument();
  });

  it("surfaces the validation error from a rejected token PUT", async () => {
    let putSeen = false;
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("/api/github/status")) {
        return { ok: false, status: 409, json: async () => ({ error: "no GitHub token configured" }) };
      }
      if (url.includes("/api/github/token") && init?.method === "PUT") {
        putSeen = true;
        return {
          ok: false,
          status: 400,
          json: async () => ({ stored: false, detail: { error: "Bad credentials" } }),
        };
      }
      return { ok: true, json: async () => [] };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <Github />
      </MemoryRouter>
    );
    await screen.findByText("Connect your GitHub account");
    await userEvent.type(screen.getByPlaceholderText(/ghp_|github_pat/), "ghp_bad");
    await userEvent.click(screen.getByRole("button", { name: "Validate & store" }));

    await waitFor(() => expect(putSeen).toBe(true));
    expect(await screen.findByText("Bad credentials")).toBeInTheDocument();
  });
});
