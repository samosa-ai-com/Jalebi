import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
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
    cli: null,
    model: null,
    enabled: true,
    notify_ntfy: true,
    created_at: "2026-08-09T10:00:00",
    updated_at: "2026-08-09T10:00:00",
    latest_run: {
      id: 1,
      screening_id: 7,
      head_sha: "abc123",
      status: "done",
      started_at: "2026-08-09T11:00:00",
      finished_at: "2026-08-09T11:01:00",
      finding_counts: { high: 1 },
      finding_total: 1,
      error: null,
    },
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

const RUNNING_RUNS = [
  {
    id: 2,
    screening_id: 7,
    head_sha: "def456",
    status: "running",
    started_at: "2026-08-09T12:00:00",
    finished_at: null,
    findings: [],
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
    if (u === "/api/repos/1/branches") {
      return {
        ok: true,
        json: async () => ({ full_name: "owner/repo", branches: ["main", "dev"] }),
      };
    }
    if (u.startsWith("/api/models")) {
      if (u.includes("cli=codex")) {
        return {
          ok: true,
          json: async () => ({ cli: "codex", models: ["gpt-5.4-mini", "gpt-5.5"] }),
        };
      }
      return {
        ok: true,
        json: async () => ({ cli: "opencode", models: ["opencode-go/deepseek-v4-flash"] }),
      };
    }
    if (u === "/api/screenings") {
      return { ok: true, json: async () => SCREENS };
    }
    if (u === "/api/screenings/7/runs") {
      return { ok: true, json: async () => RUNS };
    }
    if (u === "/api/screenings/7/run" && method === "POST") {
      return { ok: true, json: async () => ({ ok: true }) };
    }
    if (u === "/api/screenings/7" && method === "PUT") {
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

function renderScreenings() {
  return render(
    <MemoryRouter>
      <Screenings />
    </MemoryRouter>
  );
}

describe("Screenings", () => {
  it("lists screens and shows cadence", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    renderScreenings();
    expect(await screen.findByText("Security posture")).toBeInTheDocument();
    expect(screen.getByText("0 6 * * 1")).toBeInTheDocument();
  });

  it("shows a loading state before screens arrive", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Promise(() => {}))
    );
    renderScreenings();
    expect(await screen.findByText("Loading screens…")).toBeInTheDocument();
    expect(screen.queryByText("No screens yet")).not.toBeInTheDocument();
  });

  it("shows the latest-run summary on the card", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    renderScreenings();
    await screen.findByText("Security posture");
    expect(screen.getByText("done")).toBeInTheDocument();
    expect(screen.getByText("1 finding")).toBeInTheDocument();
    expect(screen.queryByText("Never run.")).not.toBeInTheDocument();
  });

  it("creates a screen from a template", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    renderScreenings();
    await screen.findByText("Security posture");
    await userEvent.click(screen.getByRole("button", { name: "New screen" }));
    await userEvent.selectOptions(
      screen.getByDisplayValue("Pick a starter screen…"),
      "Security posture"
    );
    await userEvent.selectOptions(screen.getByLabelText("Repo"), "1");
    await userEvent.click(screen.getByRole("button", { name: "Create screen" }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        (c) => c[0] === "/api/screenings" && c[1]?.method === "POST"
      );
      expect(call).toBeTruthy();
    });
  });

  it("deletes a screen after confirm", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderScreenings();
    await screen.findByText("Security posture");
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        (c) => c[0] === "/api/screenings/7" && c[1]?.method === "DELETE"
      );
      expect(call).toBeTruthy();
    });
  });

  it("shows run history with findings and creates a task after prompt review", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    renderScreenings();
    await screen.findByText("Security posture");
    await userEvent.click(screen.getByRole("button", { name: "History" }));
    expect(await screen.findByText("Secret in config")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "New task from finding" }));
    // The prompt is editable before anything is created (PRD false-positive rule).
    const editor = await screen.findByDisplayValue(/Finding \(untrusted\): Secret in config/);
    expect(editor).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.filter((c) => c[0] === "/api/tasks" && c[1]?.method === "POST")
    ).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Create task" }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        (c) => c[0] === "/api/tasks" && c[1]?.method === "POST"
      );
      expect(call).toBeTruthy();
      const body = JSON.parse(call?.[1]?.body as string);
      expect(body.type).toBe("screen_finding");
      // Finding text is piped into a fix-agent prompt as explicitly UNTRUSTED input.
      expect(body.prompt).toContain("UNTRUSTED input");
      expect(body.prompt).toContain("Finding (untrusted): Secret in config");
    });
    // Success links straight to the created task.
    const link = await screen.findByRole("link", { name: /task #99/ });
    expect(link.getAttribute("href")).toBe("/tasks/99");
  });

  it("shows the selected starter template instead of the placeholder", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    renderScreenings();
    await screen.findByText("Security posture");
    await userEvent.click(screen.getByRole("button", { name: "New screen" }));
    const tplSelect = screen.getByDisplayValue("Pick a starter screen…") as HTMLSelectElement;
    await userEvent.selectOptions(tplSelect, "Security posture");
    expect(tplSelect.value).toBe("Security posture");
  });

  it("populates the scope branch dropdown from the repo's branches", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    renderScreenings();
    await screen.findByText("Security posture");
    await userEvent.click(screen.getByRole("button", { name: "New screen" }));
    await userEvent.selectOptions(screen.getByLabelText("Repo"), "1");
    await waitFor(() => {
      const call = fetchMock.mock.calls.find((c) => c[0] === "/api/repos/1/branches");
      expect(call).toBeTruthy();
    });
    const scope = screen.getByLabelText(/Scope branch/) as HTMLSelectElement;
    expect([...scope.options].map((o) => o.value)).toEqual(expect.arrayContaining(["main", "dev"]));
  });

  it("edits a screen from the card and saves via PUT", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    renderScreenings();
    await screen.findByText("Security posture");
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(await screen.findByText("Edit screen — Security posture")).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText("Name"));
    await userEvent.type(screen.getByLabelText("Name"), "Renamed");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        (c) => c[0] === "/api/screenings/7" && c[1]?.method === "PUT"
      );
      expect(call).toBeTruthy();
      const body = JSON.parse(call?.[1]?.body as string);
      expect(body.name).toBe("Renamed");
    });
  });

  it("shows a running state instead of 'No findings.' for an in-flight run", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const u = url as string;
      if (u === "/api/screenings/templates") return { ok: true, json: async () => TEMPLATES };
      if (u === "/api/repos") return { ok: true, json: async () => REPOS };
      if (u === "/api/screenings") return { ok: true, json: async () => SCREENS };
      if (u === "/api/screenings/7/runs") return { ok: true, json: async () => RUNNING_RUNS };
      throw new Error(`unexpected fetch: ${method} ${u}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderScreenings();
    await screen.findByText("Security posture");
    await userEvent.click(screen.getByRole("button", { name: "History" }));
    expect(await screen.findByText(/Running…/)).toBeInTheDocument();
    expect(screen.queryByText("No findings.")).not.toBeInTheDocument();
  });

  it("offers opencode/codex/claude in the Backend select", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    renderScreenings();
    await screen.findByText("Security posture");
    await userEvent.click(screen.getByRole("button", { name: "New screen" }));

    const backend = screen.getByLabelText("Backend") as HTMLSelectElement;
    expect(backend.value).toBe("");
    expect([...backend.options].map((o) => o.value)).toEqual(
      expect.arrayContaining(["", "opencode", "codex", "claude"])
    );
  });

  it("refetches the Model dropdown when the Backend changes", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    renderScreenings();
    await screen.findByText("Security posture");
    await userEvent.click(screen.getByRole("button", { name: "New screen" }));

    const model = screen.getByLabelText("Model") as HTMLSelectElement;
    expect([...model.options].map((o) => o.value)).toEqual(
      expect.arrayContaining(["opencode-go/deepseek-v4-flash"])
    );

    await userEvent.selectOptions(screen.getByLabelText("Backend"), "codex");
    await waitFor(() => {
      expect([...model.options].map((o) => o.value)).toEqual(
        expect.arrayContaining(["gpt-5.4-mini", "gpt-5.5"])
      );
    });
    // The model list followed the selected backend, not the global setting.
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("cli=codex"))).toBe(true);
  });

  it("runs a screen via the run endpoint and reports failures inline", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const u = url as string;
      if (u === "/api/screenings/templates") return { ok: true, json: async () => TEMPLATES };
      if (u === "/api/repos") return { ok: true, json: async () => REPOS };
      if (u === "/api/screenings") return { ok: true, json: async () => SCREENS };
      if (u === "/api/screenings/7/run" && method === "POST") {
        return {
          ok: false,
          status: 409,
          json: async () => ({ error: "screen is already running" }),
        };
      }
      throw new Error(`unexpected fetch: ${method} ${u}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderScreenings();
    await screen.findByText("Security posture");
    await userEvent.click(screen.getByRole("button", { name: "Run now" }));
    // Correct endpoint (the old mock used /api/screenings/7) + inline error, no alert.
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          ([u, init]) => u === "/api/screenings/7/run" && init?.method === "POST"
        )
      ).toBe(true);
    });
    expect(await screen.findByText("screen is already running")).toBeInTheDocument();
  });

  it("disables Delete while a run is in flight", async () => {
    const runningScreens = [
      {
        ...SCREENS[0],
        latest_run: {
          ...SCREENS[0].latest_run,
          status: "running",
          finding_total: 0,
          finding_counts: {},
        },
      },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const u = url as string;
        if (u === "/api/screenings/templates") return { ok: true, json: async () => TEMPLATES };
        if (u === "/api/repos") return { ok: true, json: async () => REPOS };
        if (u === "/api/screenings") return { ok: true, json: async () => runningScreens };
        throw new Error(`unexpected fetch: ${u}`);
      })
    );
    renderScreenings();
    await screen.findByText("Security posture");
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
  });

  it("clears the model pin when the backend changes", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    renderScreenings();
    await screen.findByText("Security posture");
    await userEvent.click(screen.getByRole("button", { name: "New screen" }));

    const model = screen.getByLabelText("Model") as HTMLSelectElement;
    await userEvent.selectOptions(model, "opencode-go/deepseek-v4-flash");
    expect(model.value).toBe("opencode-go/deepseek-v4-flash");
    await userEvent.selectOptions(screen.getByLabelText("Backend"), "codex");
    await waitFor(() => expect(model.value).toBe(""));
  });

  it("surfaces branch-list failures in the form", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const u = url as string;
        if (u === "/api/screenings/templates") return { ok: true, json: async () => TEMPLATES };
        if (u === "/api/repos") return { ok: true, json: async () => REPOS };
        if (u === "/api/screenings") return { ok: true, json: async () => SCREENS };
        if (u === "/api/repos/1/branches") throw new Error("boom");
        if (u.startsWith("/api/models")) return { ok: true, json: async () => ({ models: [] }) };
        throw new Error(`unexpected fetch: ${u}`);
      })
    );
    renderScreenings();
    await screen.findByText("Security posture");
    await userEvent.click(screen.getByRole("button", { name: "New screen" }));
    await userEvent.selectOptions(screen.getByLabelText("Repo"), "1");
    expect(await screen.findByText(/Branch list failed to load/)).toBeInTheDocument();
  });

  it("shows history load errors instead of an empty state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const u = url as string;
        if (u === "/api/screenings/templates") return { ok: true, json: async () => TEMPLATES };
        if (u === "/api/repos") return { ok: true, json: async () => REPOS };
        if (u === "/api/screenings") return { ok: true, json: async () => SCREENS };
        if (u === "/api/screenings/7/runs") throw new Error("history boom");
        throw new Error(`unexpected fetch: ${u}`);
      })
    );
    renderScreenings();
    await screen.findByText("Security posture");
    await userEvent.click(screen.getByRole("button", { name: "History" }));
    expect(await screen.findByText(/history boom/)).toBeInTheDocument();
    expect(screen.queryByText("No runs yet.")).not.toBeInTheDocument();
  });

  it("refreshes the card once the background run row appears", async () => {
    let polls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const method = init?.method ?? "GET";
        const u = url as string;
        if (u === "/api/screenings/templates") return { ok: true, json: async () => TEMPLATES };
        if (u === "/api/repos") return { ok: true, json: async () => REPOS };
        if (u === "/api/screenings") {
          polls += 1;
          const latest =
            polls < 3
              ? null
              : {
                  id: 9,
                  screening_id: 7,
                  head_sha: "abc",
                  status: "done",
                  started_at: "2026-08-09T12:00:00",
                  finished_at: "2026-08-09T12:01:00",
                  finding_counts: {},
                  finding_total: 0,
                  error: null,
                };
          return { ok: true, json: async () => [{ ...SCREENS[0], latest_run: latest }] };
        }
        if (u === "/api/screenings/7/run" && method === "POST") {
          return { ok: true, json: async () => ({ ok: true }) };
        }
        throw new Error(`unexpected fetch: ${method} ${u}`);
      })
    );
    renderScreenings();
    await screen.findByText("Security posture");
    expect(screen.getByText("Never run.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Run now" }));
    // The waiter polls until the new row shows, then refreshes the card.
    await waitFor(
      () => {
        expect(screen.queryByText("Never run.")).not.toBeInTheDocument();
      },
      { timeout: 10000 }
    );
    expect(screen.getByText("clean")).toBeInTheDocument();
  });
});
