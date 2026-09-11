import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

describe("App", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the nav shell with all nav links", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, json: async () => [] }))
    );
    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>
    );
    expect(screen.getByText("Jalebi")).toBeInTheDocument();
    const nav = within(screen.getByRole("navigation"));
    for (const label of ["Tasks", "Repos", "GitHub", "Settings"]) {
      expect(await nav.findByRole("link", { name: label })).toBeInTheDocument();
    }
  });

  it("renders a skip to main content link targeting the main element", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, json: async () => [] }))
    );
    const { container } = render(
      <MemoryRouter>
        <App />
      </MemoryRouter>
    );
    const skipLink = screen.getByRole("link", { name: "Skip to main content" });
    expect(skipLink).toBeInTheDocument();
    expect(skipLink).toHaveAttribute("href", "#main-content");
    expect(skipLink.className).toMatch(/\bsr-only\b/);
    expect(skipLink.className).toMatch(/\bfocus:not-sr-only\b/);

    const main = container.querySelector("main#main-content");
    expect(main).toBeInTheDocument();
    // The skip target must be programmatically focusable so activating the
    // link moves keyboard focus into the page, not just scrolls.
    expect(main).toHaveAttribute("tabindex", "-1");
  });

  it("badges the Screenings tab when unseen findings exist", async () => {
    localStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (String(url).includes("/api/screenings")) {
          return {
            ok: true,
            json: async () => [
              {
                id: 7,
                latest_run: {
                  id: 1,
                  screening_id: 7,
                  status: "done",
                  finding_total: 2,
                  finding_counts: { high: 2 },
                  finished_at: new Date(Date.now() - 60000).toISOString(),
                  started_at: null,
                  head_sha: null,
                  error: null,
                },
              },
            ],
          };
        }
        return { ok: true, json: async () => [] };
      })
    );
    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>
    );
    const nav = within(screen.getByRole("navigation"));
    expect(await nav.findByText("1")).toBeInTheDocument();
  });

  it("clears the badge immediately when the Screenings tab is visited", async () => {
    localStorage.clear();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const u = String(url);
        if (u.endsWith("/api/screenings")) {
          return {
            ok: true,
            json: async () => [
              {
                id: 7,
                repo_id: 1,
                name: "S",
                system_prompt: "P",
                cadence_cron: "0 6 * * 1",
                scope_branch: null,
                cli: null,
                model: null,
                enabled: true,
                notify_ntfy: true,
                created_at: "2026-08-09T10:00:00",
                updated_at: "2026-08-09T10:00:00",
                latest_run: {
                  id: 1,
                  screening_id: 7,
                  status: "done",
                  finding_total: 2,
                  finding_counts: { high: 2 },
                  finished_at: new Date(Date.now() - 60000).toISOString(),
                  started_at: null,
                  head_sha: null,
                  error: null,
                },
              },
            ],
          };
        }
        return { ok: true, json: async () => [] };
      })
    );
    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>
    );
    const nav = within(screen.getByRole("navigation"));
    await nav.findByText("1");
    await userEvent.click(nav.getByRole("link", { name: /Screenings/ }));
    // Visiting marks seen; the badge clears without waiting for the 60s poll.
    await waitFor(() => {
      expect(nav.queryByText("1")).not.toBeInTheDocument();
    });
  });
});
