import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Screenings from "./Screenings";

const REPOS = [{ id: 1, full_name: "owner/repo", default_branch: "main" }];

const TEMPLATES = [
  {
    name: "Security posture",
    cadence_cron: "0 6 * * 1",
    system_prompt: "Audit security.",
  },
];

const SCREENS = [
  {
    id: 7,
    repo_id: 1,
    name: "Security posture",
    system_prompt: "Audit security.",
    cadence_cron: "0 6 * * 1",
    scope_branch: null,
    enabled: true,
    notify_ntfy: true,
    created_at: "2026-08-09T10:00:00",
    updated_at: "2026-08-09T10:00:00",
    latest_run: null,
  },
];

const RUNS = [
  {
    id: 1,
    screening_id: 7,
    head_sha: "abc123",
    status: "done",
    started_at: "2026-08-09T11:00:00",
    finished_at: "2026-08-09T11:01:00",
    findings: [
      {
        severity: "high",
        title: "Secret in config",
        file: "config.py",
        line: 3,
        detail: "A token is hardcoded.",
        recommendation: "Use an env var.",
      },
    ],
    output: null,
    error: null,
  },
];

function makeFetchMock() {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const u = url as string;
    if (u === "/api/screenings/templates") {
      return { ok: true, json: async () => TEMPLATES };
    }
    if (u === "/api/repos") {
      return { ok: true, json: async () => REPOS };
    }
    if (u === "/api/screenings") {
      return { ok: true, json: async () => SCREENS };
    }
    if (u === "/api/screenings/7/runs") {
      return { ok: true, json: async () => RUNS };
    }
    if (u === "/api/screenings/7" && method === "POST") {
      return { ok: true, json: async () => ({ ok: true }) };
    }
    if (u === "/api/screenings/7" && method === "DELETE") {
      return { ok: true, json: async () => ({ ok: true }) };
    }
    if (u === "/api/tasks" && method === "POST") {
      return { ok: true, json: async () => ({ id: 99 }) };
    }
    throw new Error(`unexpected fetch: ${method} ${u}`);
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Screenings", () => {
  it("lists screens and shows cadence", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    render(<Screenings />);
    expect(await screen.findByText("Security posture")).toBeInTheDocument();
    expect(screen.getByText("0 6 * * 1")).toBeInTheDocument();
  });

  it("creates a screen from a template", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Screenings />);
    await screen.findByText("Security posture");
    await userEvent.click(screen.getByRole("button", { name: "New screen" }));
    await userEvent.selectOptions(
      screen.getByDisplayValue("Pick a starter screen…"),
      "Security posture"
    );
    await userEvent.selectOptions(screen.getByLabelText("Repo"), "1");
    await userEvent.click(screen.getByRole("button", { name: "Create screen" }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find((c) => c[0] === "/api/screenings" && c[1]?.method === "POST");
      expect(call).toBeTruthy();
    });
  });

  it("deletes a screen after confirm", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<Screenings />);
    await screen.findByText("Security posture");
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find((c) => c[0] === "/api/screenings/7" && c[1]?.method === "DELETE");
      expect(call).toBeTruthy();
    });
  });

  it("shows run history with findings and can create a task from a finding", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Screenings />);
    await screen.findByText("Security posture");
    await userEvent.click(screen.getByRole("button", { name: "History" }));
    expect(await screen.findByText("Secret in config")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "New task from finding" }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find((c) => c[0] === "/api/tasks" && c[1]?.method === "POST");
      expect(call).toBeTruthy();
      const body = JSON.parse(call?.[1]?.body as string);
      expect(body.type).toBe("screen_finding");
    });
  });
});
