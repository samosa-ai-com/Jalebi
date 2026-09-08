import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import Screenings from "./Screenings";
import { buildMultiFindingPrompt, MULTI_PROMPT_SOFT_CAP } from "../lib/screeningPrompt";

const { navigateMock } = vi.hoisted(() => ({ navigateMock: vi.fn() }));

vi.mock("react-router-dom", async (importOriginal) => {
  const mod = await importOriginal<typeof import("react-router-dom")>();
  return { ...mod, useNavigate: () => navigateMock };
});

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

const FINDINGS = [
  {
    screen_id: 7,
    screen_name: "Security posture",
    repo_id: 1,
    repo_full_name: "owner/repo",
    run_id: 1,
    head_sha: "abc123",
    finished_at: "2026-08-09T11:01:00",
    severity: "high",
    title: "Secret in config",
    file: "config.py",
    line: 3,
    detail: "A token is hardcoded.",
    recommendation: "Use an env var.",
  },
  {
    screen_id: 7,
    screen_name: "Security posture",
    repo_id: 1,
    repo_full_name: "owner/repo",
    run_id: 1,
    head_sha: "abc123",
    finished_at: "2026-08-09T11:01:00",
    severity: "low",
    title: "Stale comment",
    file: null,
    line: null,
    detail: null,
    recommendation: null,
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
    if (u.startsWith("/api/screenings/findings")) {
      const qs = u.split("?")[1] ?? "";
      const params = new URLSearchParams(qs);
      const sev = params.get("severity");
      const sid = params.get("screen_id");
      return {
        ok: true,
        json: async () =>
          FINDINGS.filter(
            (f) => (!sev || f.severity === sev) && (!sid || String(f.screen_id) === sid)
          ),
      };
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
    if (u.startsWith("/api/screenings/dealt")) {
      if (method === "GET") return { ok: true, json: async () => ({ fingerprints: [] }) };
      return { ok: true, json: async () => ({ marked: 1, reopened: 1 }) };
    }
    throw new Error(`unexpected fetch: ${method} ${u}`);
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  navigateMock.mockClear();
});

function renderScreenings() {
  return render(
    <MemoryRouter>
      <Screenings />
    </MemoryRouter>
  );
}

async function goScreens() {
  await userEvent.click(screen.getByRole("button", { name: "screens" }));
}

describe("Screenings", () => {
  it("lists screens and shows cadence", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    renderScreenings();
    await goScreens();
    expect(await screen.findByText("owner/repo · Security posture")).toBeInTheDocument();
    expect(screen.getByText("0 6 * * 1")).toBeInTheDocument();
  });

  it("shows a loading state before screens arrive", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Promise(() => {}))
    );
    renderScreenings();
    await goScreens();
    expect(await screen.findByText("Loading screens…")).toBeInTheDocument();
    expect(screen.queryByText("No screens yet")).not.toBeInTheDocument();
  });

  it("shows the latest-run summary on the card", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    renderScreenings();
    await goScreens();
    await screen.findByText("owner/repo · Security posture");
    expect(screen.getByText("done")).toBeInTheDocument();
    expect(screen.getByText("1 finding")).toBeInTheDocument();
    expect(screen.queryByText("Never run.")).not.toBeInTheDocument();
  });

  it("creates a screen from a template", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    renderScreenings();
    await goScreens();
    await screen.findByText("owner/repo · Security posture");
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
    await goScreens();
    await screen.findByText("owner/repo · Security posture");
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        (c) => c[0] === "/api/screenings/7" && c[1]?.method === "DELETE"
      );
      expect(call).toBeTruthy();
    });
  });

  it("sends a history finding to the New-task form with the prompt injected", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    renderScreenings();
    await goScreens();
    await screen.findByText("owner/repo · Security posture");
    await userEvent.click(screen.getByRole("button", { name: "History" }));
    expect(await screen.findByText("Secret in config")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "New task from finding" }));
    // Handoff: exactly one navigation carrying the prefill — no direct POST.
    await waitFor(() => expect(navigateMock).toHaveBeenCalledTimes(1));
    const [to, opts] = navigateMock.mock.calls[0] as [string, { state: Record<string, unknown> }];
    expect(to).toBe("/");
    const state = opts.state as {
      prefill: Record<string, unknown>;
      dealtFps: string[];
      from: string;
    };
    expect(state.from).toBe("screenings");
    expect(state.prefill).toMatchObject({
      repoId: 1,
      type: "freeform",
      publishMode: "manual",
    });
    expect(state.prefill.prompt as string).toContain("UNTRUSTED input");
    expect(state.prefill.prompt as string).toContain("Finding (untrusted): Secret in config");
    expect(state.dealtFps).toHaveLength(1);
    expect(
      fetchMock.mock.calls.filter((c) => c[0] === "/api/tasks" && c[1]?.method === "POST")
    ).toHaveLength(0);
  });

  it("opens one batched task from selected history findings", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    renderScreenings();
    await goScreens();
    await screen.findByText("owner/repo · Security posture");
    await userEvent.click(screen.getByRole("button", { name: "History" }));
    await screen.findByText("Secret in config");
    await userEvent.click(screen.getByLabelText("Select finding Secret in config"));
    expect(await screen.findByText("1 selected")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Open new task with finding" }));
    await waitFor(() => expect(navigateMock).toHaveBeenCalledTimes(1));
    const [, opts] = navigateMock.mock.calls[0] as [string, { state: Record<string, unknown> }];
    const state = opts.state as { prefill: Record<string, unknown>; dealtFps: string[] };
    expect(state.prefill.prompt as string).toContain("Secret in config");
    expect(state.dealtFps).toHaveLength(1);
    expect(
      fetchMock.mock.calls.filter((c) => c[0] === "/api/tasks" && c[1]?.method === "POST")
    ).toHaveLength(0);
  });

  it("shows the selected starter template instead of the placeholder", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    renderScreenings();
    await goScreens();
    await screen.findByText("owner/repo · Security posture");
    await userEvent.click(screen.getByRole("button", { name: "New screen" }));
    const tplSelect = screen.getByDisplayValue("Pick a starter screen…") as HTMLSelectElement;
    await userEvent.selectOptions(tplSelect, "Security posture");
    expect(tplSelect.value).toBe("Security posture");
  });

  it("populates the scope branch dropdown from the repo's branches", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    renderScreenings();
    await goScreens();
    await screen.findByText("owner/repo · Security posture");
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
    await goScreens();
    await screen.findByText("owner/repo · Security posture");
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
    await goScreens();
    await screen.findByText("owner/repo · Security posture");
    await userEvent.click(screen.getByRole("button", { name: "History" }));
    expect(await screen.findByText(/Running…/)).toBeInTheDocument();
    expect(screen.queryByText("No findings.")).not.toBeInTheDocument();
  });

  it("offers opencode/codex/claude in the Backend select", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    renderScreenings();
    await goScreens();
    await screen.findByText("owner/repo · Security posture");
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
    await goScreens();
    await screen.findByText("owner/repo · Security posture");
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
    await goScreens();
    await screen.findByText("owner/repo · Security posture");
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
    await goScreens();
    await screen.findByText("owner/repo · Security posture");
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
  });

  it("clears the model pin when the backend changes", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    renderScreenings();
    await goScreens();
    await screen.findByText("owner/repo · Security posture");
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
    await goScreens();
    await screen.findByText("owner/repo · Security posture");
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
    await goScreens();
    await screen.findByText("owner/repo · Security posture");
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
    await goScreens();
    await screen.findByText("owner/repo · Security posture");
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

  it("shows a unified findings inbox with severity filter", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    renderScreenings();
    await screen.findByText("owner/repo · Security posture");
    await userEvent.click(screen.getByRole("button", { name: "findings" }));
    expect(await screen.findByText("Stale comment")).toBeInTheDocument();
    expect(screen.getByText("Secret in config")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "high" }));
    expect(screen.queryByText("Stale comment")).not.toBeInTheDocument();
    expect(screen.getByText("Secret in config")).toBeInTheDocument();
  });

  it("opens the New-task form from an inbox finding without creating yet", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    renderScreenings();
    await screen.findByText("owner/repo · Security posture");
    await userEvent.click(screen.getByRole("button", { name: "findings" }));
    await screen.findByText("Secret in config");
    await userEvent.click(screen.getByText("Secret in config"));
    await userEvent.click(screen.getAllByRole("button", { name: "New task from finding" })[0]);
    await waitFor(() => expect(navigateMock).toHaveBeenCalledTimes(1));
    const [to, opts] = navigateMock.mock.calls[0] as [string, { state: Record<string, unknown> }];
    expect(to).toBe("/");
    const state = opts.state as { prefill: Record<string, unknown>; dealtFps: string[] };
    expect(state.prefill).toMatchObject({ repoId: 1, type: "freeform", publishMode: "manual" });
    expect(state.prefill.prompt as string).toContain("Finding (untrusted): Secret in config");
    // Nothing is created (and nothing marked dealt) until the form submits.
    expect(
      fetchMock.mock.calls.filter((c) => c[0] === "/api/tasks" && c[1]?.method === "POST")
    ).toHaveLength(0);
    expect(screen.getByText("Secret in config")).toBeInTheDocument();
  });

  it("opens one combined task from a batch of inbox findings", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    renderScreenings();
    await screen.findByText("owner/repo · Security posture");
    await userEvent.click(screen.getByRole("button", { name: "findings" }));
    await screen.findByText("Stale comment");
    await userEvent.click(screen.getByLabelText("Select all visible findings"));
    expect(await screen.findByText("2 selected")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Open new task with 2 findings" }));
    await waitFor(() => expect(navigateMock).toHaveBeenCalledTimes(1));
    const [, opts] = navigateMock.mock.calls[0] as [string, { state: Record<string, unknown> }];
    const state = opts.state as { prefill: Record<string, unknown>; dealtFps: string[] };
    const prompt = state.prefill.prompt as string;
    expect(prompt).toContain("Fix these 2 findings");
    expect(prompt).toContain("Secret in config");
    expect(prompt).toContain("Stale comment");
    expect(state.dealtFps).toHaveLength(2);
    expect(
      fetchMock.mock.calls.filter((c) => c[0] === "/api/tasks" && c[1]?.method === "POST")
    ).toHaveLength(0);
  });

  it("disambiguates identical screen names by repo in the filter dropdown", async () => {
    const twin = { ...SCREENS[0], id: 8, repo_id: 2, name: "Security posture" };
    const twinRepos = [...REPOS, { id: 2, full_name: "other/repo", default_branch: "main" }];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const u = url as string;
        if (u === "/api/screenings/templates") return { ok: true, json: async () => TEMPLATES };
        if (u === "/api/repos") return { ok: true, json: async () => twinRepos };
        if (u === "/api/screenings") return { ok: true, json: async () => [SCREENS[0], twin] };
        if (u.startsWith("/api/screenings/findings"))
          return { ok: true, json: async () => FINDINGS };
        throw new Error(`unexpected fetch: ${u}`);
      })
    );
    renderScreenings();
    await screen.findByText("owner/repo · Security posture");
    const options = [...screen.getByLabelText("Filter by screen").querySelectorAll("option")].map(
      (o) => o.textContent
    );
    expect(options).toContain("owner/repo · Security posture");
    expect(options).toContain("other/repo · Security posture");
  });

  it("refuses a batch spanning two repos with guidance", async () => {
    const otherScreen = { ...SCREENS[0], id: 8, repo_id: 2, name: "Other screen" };
    const mixed = [
      ...FINDINGS,
      { ...FINDINGS[0], screen_id: 8, screen_name: "Other screen", repo_id: 2 },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const u = url as string;
        if (u === "/api/screenings/templates") return { ok: true, json: async () => TEMPLATES };
        if (u === "/api/repos") return { ok: true, json: async () => REPOS };
        if (u === "/api/screenings")
          return { ok: true, json: async () => [SCREENS[0], otherScreen] };
        if (u.startsWith("/api/screenings/findings")) return { ok: true, json: async () => mixed };
        throw new Error(`unexpected fetch: ${u}`);
      })
    );
    renderScreenings();
    await screen.findByText("owner/repo · Security posture");
    await userEvent.click(screen.getByRole("button", { name: "findings" }));
    await screen.findByText("Stale comment");
    await userEvent.click(screen.getByLabelText("Select all visible findings"));
    expect(await screen.findByText("3 selected")).toBeInTheDocument();
    const taskBtn = screen.getByRole("button", { name: "Open new task with 3 findings" });
    expect(taskBtn).toBeDisabled();
    expect(await screen.findByText(/span 2 repos/)).toBeInTheDocument();
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it("shows a health banner when a screen is failing", async () => {
    const failingScreens = [
      {
        ...SCREENS[0],
        latest_run: { ...SCREENS[0].latest_run, status: "failed", error: "agent exploded" },
      },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const u = url as string;
        if (u === "/api/screenings/templates") return { ok: true, json: async () => TEMPLATES };
        if (u === "/api/repos") return { ok: true, json: async () => REPOS };
        if (u === "/api/screenings") return { ok: true, json: async () => failingScreens };
        throw new Error(`unexpected fetch: ${u}`);
      })
    );
    renderScreenings();
    expect(await screen.findByText(/1 of 1 screen failing/)).toBeInTheDocument();
  });

  it("filters the inbox by text and shows the empty state", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    renderScreenings();
    await screen.findByText("owner/repo · Security posture");
    await userEvent.click(screen.getByRole("button", { name: "findings" }));
    await screen.findByText("Secret in config");
    await userEvent.type(screen.getByLabelText("Filter findings"), "stale");
    expect(screen.queryByText("Secret in config")).not.toBeInTheDocument();
    expect(screen.getByText("Stale comment")).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText("Filter findings"));
    await userEvent.type(screen.getByLabelText("Filter findings"), "zzz-no-match");
    expect(await screen.findByText("No findings match the filter.")).toBeInTheDocument();
  });

  it("shows the empty inbox state when there are no findings", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const u = url as string;
        if (u === "/api/screenings/templates") return { ok: true, json: async () => TEMPLATES };
        if (u === "/api/repos") return { ok: true, json: async () => REPOS };
        if (u === "/api/screenings") return { ok: true, json: async () => SCREENS };
        if (u.startsWith("/api/screenings/findings")) return { ok: true, json: async () => [] };
        throw new Error(`unexpected fetch: ${u}`);
      })
    );
    renderScreenings();
    await goScreens();
    await screen.findByText("owner/repo · Security posture");
    await userEvent.click(screen.getByRole("button", { name: "findings" }));
    expect(await screen.findByText(/Clean audits, quiet inbox/)).toBeInTheDocument();
  });

  it("filters the inbox by screen", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    renderScreenings();
    await screen.findByText("owner/repo · Security posture");
    await userEvent.click(screen.getByRole("button", { name: "findings" }));
    await screen.findByText("Secret in config");
    await userEvent.selectOptions(screen.getByLabelText("Filter by screen"), "7");
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes("screen_id=7"))).toBe(true);
    });
  });

  it("marks a batch dealt in one go and hides the findings", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    renderScreenings();
    await screen.findByText("owner/repo · Security posture");
    await screen.findByText("Secret in config");
    await userEvent.click(screen.getByLabelText("Select all visible findings"));
    expect(await screen.findByText("2 selected")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Mark dealt (2)" }));
    // The batch marks via one API call per screen, then both hide at once.
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          (c) => c[0] === "/api/screenings/dealt" && c[1]?.method === "POST"
        )
      ).toBe(true);
    });
    await waitFor(() => {
      expect(screen.queryByText("Secret in config")).not.toBeInTheDocument();
    });
    expect(screen.queryByText("Stale comment")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Hide dealt \(2\)/ })).toBeInTheDocument();
  });

  it("imports the legacy browser dealt set once, then clears it", async () => {
    const legacy = JSON.stringify([JSON.stringify([7, "Secret in config", "config.py", 3])]);
    localStorage.setItem("jalebi-findings-dealt", legacy);
    const serverDealt = new Set<string>();
    const base = makeFetchMock();
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (typeof url === "string" && url.startsWith("/api/screenings/dealt")) {
        if (method === "GET") {
          return { ok: true, json: async () => ({ fingerprints: [...serverDealt] }) };
        }
        const body = JSON.parse((init?.body as string) ?? "{}") as { fps?: string[] };
        for (const fp of body.fps ?? []) serverDealt.add(fp);
        return { ok: true, json: async () => ({ marked: 1, reopened: 1 }) };
      }
      return base(url, init);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderScreenings();
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          (c) => c[0] === "/api/screenings/dealt/import" && c[1]?.method === "POST"
        )
      ).toBe(true);
    });
    expect(localStorage.getItem("jalebi-dealt-imported")).toBe("1");
    expect(localStorage.getItem("jalebi-findings-dealt")).toBeNull();
    // The imported finding hides once the server set revalidates.
    await waitFor(() => {
      expect(screen.queryByText("Secret in config")).not.toBeInTheDocument();
    });
  });

  it("rolls back an optimistic mark when the API fails", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (
          typeof url === "string" &&
          url.startsWith("/api/screenings/dealt") &&
          init?.method === "POST"
        ) {
          return { ok: false, json: async () => ({ error: "db locked" }) };
        }
        return fetchMock(url, init);
      })
    );
    renderScreenings();
    await screen.findByText("owner/repo · Security posture");
    await screen.findByText("Secret in config");
    await userEvent.click(screen.getByText("Stale comment"));
    await userEvent.click(screen.getAllByRole("button", { name: "Mark dealt" })[0]);
    expect(await screen.findByText("Could not update dealt state — retry.")).toBeInTheDocument();
    // Rolled back: the finding stays visible.
    expect(screen.getByText("Stale comment")).toBeInTheDocument();
  });

  it("reveals and reopens dealt findings via the toggle", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    renderScreenings();
    await screen.findByText("owner/repo · Security posture");
    await screen.findByText("Secret in config");
    await userEvent.click(screen.getByText("Stale comment"));
    await userEvent.click(screen.getAllByRole("button", { name: "Mark dealt" })[0]);
    await waitFor(() => {
      expect(screen.queryByText("Stale comment")).not.toBeInTheDocument();
    });
    await userEvent.click(screen.getByRole("button", { name: /Hide dealt/ }));
    expect(await screen.findByText("Stale comment")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Reopen" }));
    // Reopened while showing dealt: still visible; hiding again keeps it (not dealt).
    expect(screen.getByText("Stale comment")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show dealt" }));
    expect(screen.getByText("Stale comment")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Hide dealt \(\d+\)/ })).not.toBeInTheDocument();
  });

  it("opens the Screens view only on click (Findings is default)", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    renderScreenings();
    // Findings tab content loads without visiting Screens.
    expect(await screen.findByText("Secret in config")).toBeInTheDocument();
    expect(screen.queryByText("Never run.")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "screens" }));
    expect(await screen.findByText("0 6 * * 1")).toBeInTheDocument();
  });

  it("truncates an oversized batch prompt with a marker", async () => {
    const screen = SCREENS[0] as unknown as Parameters<
      typeof buildMultiFindingPrompt
    >[0][number]["screen"];
    const entries = Array.from({ length: 10 }, (_, i) => ({
      screen,
      finding: {
        severity: "high" as const,
        title: `Finding ${i}`,
        file: "big.py",
        line: i,
        detail: "x".repeat(1500),
        recommendation: "y".repeat(500),
      },
    }));
    const prompt = buildMultiFindingPrompt(entries);
    expect(prompt.length).toBeLessThanOrEqual(MULTI_PROMPT_SOFT_CAP + 200);
    expect(prompt).toContain("Finding 1 of 10");
    expect(prompt).toContain("truncated");
  });

  it("renders '← Back to Mission control' when navigating with from: mission", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    render(
      <MemoryRouter initialEntries={[{ pathname: "/screenings", state: { from: "mission" } }]}>
        <Screenings />
      </MemoryRouter>
    );
    expect(
      await screen.findByRole("link", { name: /Back to Mission control/i })
    ).toBeInTheDocument();
  });
});
