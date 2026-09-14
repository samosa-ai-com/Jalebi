import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Settings from "./Settings";

const SETTINGS = {
  concurrency: 4,
  auto_publish: true,
  ntfy_topic: "",
  default_timeout_minutes: 60,
  retry_policy: {
    auto_retry: false,
    continue_prompt: "continue",
    timeout_multiplier: 2,
    max_timeout_minutes: 180,
  },
  stall_timeout_seconds: 600,
  secret_patterns: [],
  artifact_ttl_days: 7,
  default_backend: "opencode",
  default_model: "",
  adapter_model_lists: {},
  enabled_backends: ["opencode", "codex", "claude"],
  auto_nudge: false,
  notify_on_done: true,
  notify_on_failed: true,
  notify_on_progress: true,
  notify_on_needs_approval: true,
  notify_progress_interval_minutes: 30,
  webhook_url: "",
  webhook_secret: "",
  timezone: "local",
  ide_command: "",
  ide_name: "",
};

function makeFetchMock(settingsOverrides: Partial<typeof SETTINGS> = {}, models: string[] = []) {
  const currentSettings = { ...SETTINGS, ...settingsOverrides };
  return vi.fn(async (url: string, init?: RequestInit) => {
    if (String(url).includes("/api/models")) {
      const cli = new URL(String(url), "http://localhost").searchParams.get("cli");
      return { ok: true, json: async () => ({ cli: cli ?? "opencode", models }) };
    }
    if (String(url).includes("/api/backends")) {
      return {
        ok: true,
        json: async () => ({
          backends: ["opencode", "codex", "claude"],
          enabled: ["opencode", "codex", "claude"],
          default: "opencode",
        }),
      };
    }
    if (String(url).includes("/api/timezones")) {
      return {
        ok: true,
        json: async () => ({
          local: "local",
          common: ["Asia/Kolkata"],
          all: ["Asia/Kolkata", "UTC"],
        }),
      };
    }
    if (String(url).includes("/api/data/usage")) {
      return {
        ok: true,
        json: async () => ({ sizes: {}, counts: {}, tasks_by_status: {} }),
      };
    }
    if (String(url).includes("/restore")) {
      const body = init?.method === "POST" ? JSON.parse((init.body as string) || "{}") : {};
      if (body.dry_run === false && body.confirm === "RESTORE") {
        return {
          ok: true,
          json: async () => ({
            dry_run: false,
            preview: {
              backup: "data-20260101-000000.db",
              integrity_ok: true,
              integrity_detail: null,
              busy_tasks: 0,
              busy_screenings: 0,
            },
            restored: "data-20260101-000000.db",
            safety_backup: "data-20260102-000000.db",
          }),
        };
      }
      return {
        ok: true,
        json: async () => ({
          dry_run: true,
          preview: {
            backup: "data-20260101-000000.db",
            integrity_ok: true,
            integrity_detail: null,
            busy_tasks: 0,
            busy_screenings: 0,
          },
        }),
      };
    }
    if (String(url).includes("/api/data/backups")) {
      return {
        ok: true,
        json: async () => [
          { name: "data-20260101-000000.db", size: 1234, created_at: "2026-01-01" },
        ],
      };
    }
    if (String(url).includes("/api/data/prune")) {
      return {
        ok: true,
        json: async () => ({
          dry_run: true,
          preview: {
            cutoff: "2026-01-01",
            tasks: { task_ids: [], count: 0 },
            runs: 0,
            task_events: 0,
            deliveries: 0,
            screening_runs: 0,
            orphan_worktrees: [],
            orphan_artifacts: [],
            old_logs: 0,
          },
        }),
      };
    }
    if (String(url).includes("/api/notify/test")) {
      return { ok: true, json: async () => ({ ok: true }) };
    }
    if (String(url).includes("/api/ide/status")) {
      return {
        ok: true,
        json: async () => ({
          command: currentSettings.ide_command,
          name: currentSettings.ide_name,
          found: Boolean(currentSettings.ide_command),
        }),
      };
    }
    if (String(url).includes("/api/ide/detect")) {
      return {
        ok: true,
        json: async () => ({
          command: "code",
          name: "Visual Studio Code",
          detected: [
            { command: "code", name: "Visual Studio Code", path: "/usr/bin/code" },
            {
              command: "antigravity",
              name: "Google Antigravity",
              path: "/usr/local/bin/antigravity",
            },
          ],
        }),
      };
    }
    if (String(url).includes("/api/ide/test")) {
      return { ok: true, json: async () => ({ ok: true }) };
    }
    if (String(url).includes("/api/settings") && init?.method === "POST") {
      const body = JSON.parse(init.body as string) as Record<string, unknown>;
      return { ok: true, json: async () => ({ [String(body.key)]: body.value }) };
    }
    if (String(url).includes("/api/envvars")) {
      return { ok: true, json: async () => [] };
    }
    if (String(url).includes("/api/repos")) {
      return { ok: true, json: async () => [] };
    }
    return { ok: true, json: async () => currentSettings };
  });
}

describe("Settings", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  async function expand(name: RegExp) {
    await screen.findByLabelText("Filter settings");
    await userEvent.click(screen.getByRole("button", { name }));
  }

  it("starts with every section collapsed", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Settings />);
    expect(await screen.findByText("Settings")).toBeInTheDocument();
    expect(screen.queryByText("Queue concurrency")).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText("my-jalebi")).not.toBeInTheDocument();
  });

  it("deep-links ?section=queue straight to Queue concurrency", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    window.history.pushState({}, "", "/settings?section=queue");
    try {
      render(<Settings />);
      expect(await screen.findByText("Queue concurrency")).toBeInTheDocument();
      expect(document.getElementById("settings-section-queue")).not.toBeNull();
      // The deep-linked section stays user-collapsible.
      await userEvent.click(screen.getByRole("button", { name: /Queue & timeouts/ }));
      expect(screen.queryByText("Queue concurrency")).not.toBeInTheDocument();
    } finally {
      window.history.pushState({}, "", "/settings");
    }
  });

  it("loads settings and toggles auto_publish via POST", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    render(<Settings />);
    await expand(/Queue & timeouts/);
    expect(await screen.findByText("Queue concurrency")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("switch", { name: "Auto-publish PRs" }));

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        (call) =>
          typeof call[0] === "string" &&
          call[0].includes("/api/settings") &&
          call[1]?.method === "POST"
      );
      expect(postCall).toBeTruthy();
      const body = JSON.parse(postCall![1]!.body as string) as Record<string, unknown>;
      expect(body).toEqual({ key: "auto_publish", value: false });
    });
  });

  it("shows the Notifications section with ntfy endpoint and test button", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    render(<Settings />);
    await expand(/Notifications/);
    expect(await screen.findByText("Notifications")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("my-jalebi")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Send test notification" }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find((c) => String(c[0]).includes("/api/notify/test"));
      expect(call).toBeTruthy();
      expect(call![1]?.method).toBe("POST");
    });
    expect(await screen.findByText("sent")).toBeInTheDocument();
  });

  it("labels the timeout setting 'Timeout' not 'Default timeout'", async () => {
    localStorage.setItem("jalebi-settings-show-advanced-v1", "1");
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    render(<Settings />);
    await expand(/Queue & timeouts/);
    expect(await screen.findByText("Timeout")).toBeInTheDocument();
    expect(screen.queryByText("Default timeout")).not.toBeInTheDocument();
  });

  it("toggling auto-recovery preserves the other retry_policy keys", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    render(<Settings />);
    await expand(/Recovery/);
    await screen.findByText("Auto-recovery");

    await userEvent.click(screen.getByRole("switch", { name: "Auto-recovery" }));

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        ([url, init]) => String(url).includes("/api/settings") && init?.method === "POST"
      );
      expect(postCall).toBeTruthy();
      const body = JSON.parse((postCall?.[1] as RequestInit).body as string) as {
        key: string;
        value: Record<string, unknown>;
      };
      expect(body.key).toBe("retry_policy");
      expect(body.value.auto_retry).toBe(true);
      expect(body.value.continue_prompt).toBe("continue");
      expect(body.value.timeout_multiplier).toBe(2);
      expect(body.value.max_timeout_minutes).toBe(180);
    });
  });

  it("shows the stall-timeout and continue-prompt recovery fields", async () => {
    localStorage.setItem("jalebi-settings-show-advanced-v1", "1");
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    render(<Settings />);
    await expand(/Queue & timeouts/);
    expect(await screen.findByText("Stall timeout")).toBeInTheDocument();
    await expand(/Recovery/);
    expect(screen.getByText("Continue prompt")).toBeInTheDocument();
    expect(screen.getByText("Timeout multiplier")).toBeInTheDocument();
    expect(screen.getByText("Max timeout")).toBeInTheDocument();
  });

  it("offers opencode/codex/claude in the Default backend select", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    render(<Settings />);
    await expand(/Agent defaults/);
    await screen.findByText("Default backend");

    // The select is unlabeled — scope it by its section heading.
    const section = screen.getByRole("heading", { name: "Default backend" }).closest("section")!;
    const backendBtn = within(section).getByRole("button", { name: "Default backend" });

    expect(backendBtn).toHaveTextContent("opencode");
    await userEvent.click(backendBtn);
    expect(screen.getByRole("option", { name: "opencode" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "codex" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "claude" })).toBeInTheDocument();
  });

  it("keeps the row lift classes scanner-visible (trailing space matters)", async () => {
    // Regression guard: Tailwind v4 only generates `focus-within:z-30` when it
    // appears as a whitespace-separated token. Without the space the open
    // dropdown renders behind the next card (verified live with screenshots).
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    render(<Settings />);
    await expand(/Agent defaults/);
    await screen.findByText("Default backend");

    const section = screen.getByRole("heading", { name: "Default backend" }).closest("section")!;
    // Lift engages on focus AND while any inner dropdown reports open
    // (aria-expanded), so focus loss with an open list can't drop it behind.
    for (const token of [
      "focus-within:relative",
      "focus-within:z-30",
      'has-[[aria-expanded="true"]]:relative',
      'has-[[aria-expanded="true"]]:z-30',
    ]) {
      expect(section.className.split(/\s+/)).toContain(token);
    }
  });

  it("clicking the Backend caption opens the dropdown (label activation)", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    render(<Settings />);
    await expand(/Agent defaults/);
    await screen.findByText("Default backend");

    const section = screen.getByRole("heading", { name: "Default backend" }).closest("section")!;
    // Click the visible caption text, not the control: the wrapping label must
    // forward activation to the nested select.
    await userEvent.click(within(section).getByText("Backend", { exact: true }));
    expect(await screen.findByRole("option", { name: "opencode" })).toBeInTheDocument();
  });
});

// ---- Phase 4 T6 — IDE settings section ---------------------------------

describe("Settings (Phase 4 T6 — IDE)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("renders the IDE section with detected IDEs", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Settings />);
    expect(await screen.findByText("IDE")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /IDE/ }));
    expect(await screen.findByText("Visual Studio Code")).toBeInTheDocument();
    expect(screen.getByText("Google Antigravity")).toBeInTheDocument();
    expect(screen.getByText("Custom command…")).toBeInTheDocument();
  });

  it("selecting a detected IDE saves ide_command and ide_name", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Settings />);
    await screen.findByText("IDE");
    await userEvent.click(screen.getByRole("button", { name: /IDE/ }));
    const antBtn = await screen.findByText("Google Antigravity");
    await userEvent.click(antBtn);
    await waitFor(() => {
      const calls = fetchMock.mock.calls.filter(
        ([url, init]) => String(url).includes("/api/settings") && init?.method === "POST"
      );
      const keys = calls.map(([, init]) => JSON.parse((init?.body as string) || "{}"));
      expect(keys.some((k) => k.key === "ide_command" && k.value === "antigravity")).toBe(true);
      expect(keys.some((k) => k.key === "ide_name" && k.value === "Google Antigravity")).toBe(true);
    });
  });

  it("Test open POSTs to /api/ide/test when configured", async () => {
    const fetchMock = makeFetchMock({ ide_command: "code", ide_name: "Visual Studio Code" });
    vi.stubGlobal("fetch", fetchMock);
    render(<Settings />);
    await screen.findByText("IDE");
    await userEvent.click(screen.getByRole("button", { name: /IDE/ }));
    const testBtn = await screen.findByText("Test open");
    expect(testBtn).not.toBeDisabled();
    await userEvent.click(testBtn);
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) => String(url).includes("/api/ide/test") && init?.method === "POST"
        )
      ).toBe(true);
    });
  });

  it("offers Custom command when no IDEs are detected", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (String(url).includes("/api/ide/detect")) {
          return { ok: true, json: async () => ({ command: "", name: "", detected: [] }) };
        }
        return (fetchMock as unknown as (u: string, i?: RequestInit) => Promise<unknown>)(
          url,
          init
        );
      })
    );
    render(<Settings />);
    await screen.findByText("IDE");
    await userEvent.click(screen.getByRole("button", { name: /IDE/ }));
    expect(await screen.findByText("Custom command…")).toBeInTheDocument();
  });

  it("typing an IDE command does not POST until blur", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Settings />);
    await screen.findByText("IDE");
    await userEvent.click(screen.getByRole("button", { name: /IDE/ }));
    await screen.findByText("Custom command…");
    await userEvent.click(screen.getByText("Custom command…"));
    const input = await screen.findByPlaceholderText("e.g. cursor, code, nvim, /usr/bin/zed");
    await userEvent.type(input, "myide");
    const posts = () =>
      fetchMock.mock.calls.filter(
        ([url, init]) => String(url).includes("/api/settings") && init?.method === "POST"
      );
    expect(posts().length).toBe(0);
    await userEvent.tab();
    await waitFor(() => expect(posts().length).toBeGreaterThan(0));
  });
});

// ---- Settings overhaul: recovery + data -----------------------------------

describe("Settings (recovery + data)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  async function expand(name: RegExp) {
    await screen.findByLabelText("Filter settings");
    await userEvent.click(screen.getByRole("button", { name }));
  }

  it("shows Recovery attempts and saves max_attempts", async () => {
    localStorage.setItem("jalebi-settings-show-advanced-v1", "1");
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Settings />);
    await expand(/Recovery/);
    expect(await screen.findByText("Recovery attempts")).toBeInTheDocument();
    const input = screen.getByRole("spinbutton", { name: "Recovery attempts" });
    await userEvent.clear(input);
    await userEvent.type(input, "5");
    await userEvent.tab();

    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        ([url, init]) => String(url).includes("/api/settings") && init?.method === "POST"
      );
      expect(postCall).toBeTruthy();
      const body = JSON.parse((postCall?.[1] as RequestInit).body as string) as {
        key: string;
        value: Record<string, unknown>;
      };
      expect(body.key).toBe("retry_policy");
      expect(body.value.max_attempts).toBe(5);
    });
  });

  it("warns when the default model is not in the backend list", async () => {
    const fetchMock = makeFetchMock({ default_model: "gpt-wrong" }, ["gpt-a", "gpt-b"]);
    vi.stubGlobal("fetch", fetchMock);
    render(<Settings />);
    await expand(/Agent defaults/);
    expect(
      await screen.findByText(/current value isn't in this backend's list/)
    ).toBeInTheDocument();
  });

  it("renders the Data management section with storage and prune", async () => {
    localStorage.setItem("jalebi-settings-show-advanced-v1", "1");
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Settings />);
    expect(await screen.findByText("Data management")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Data management/ }));
    expect(await screen.findByText("Back up now")).toBeInTheDocument();
    expect(screen.getByText("Clean up old data")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/api/data/usage"))).toBe(
      true
    );
  });

  it("search narrows to matching rows and auto-opens the section", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Settings />);
    await screen.findByText("Settings");
    await userEvent.type(screen.getByLabelText("Filter settings"), "concurrency");
    expect(await screen.findByText("Queue concurrency")).toBeInTheDocument();
    expect(screen.queryByText("Auto-publish PRs")).not.toBeInTheDocument();
    expect(screen.queryByText("Recovery attempts")).not.toBeInTheDocument();
  });

  it("search with no matches shows an empty note; clear restores", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Settings />);
    await screen.findByText("Settings");
    await userEvent.type(screen.getByLabelText("Filter settings"), "zzz-no-such-setting");
    expect(await screen.findByText(/No settings match/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Clear filter" }));
    expect(screen.queryByText(/No settings match/)).not.toBeInTheDocument();
    expect(screen.queryByText("Queue concurrency")).not.toBeInTheDocument();
  });

  it("expand all opens every section at once", async () => {
    localStorage.setItem("jalebi-settings-show-advanced-v1", "1");
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Settings />);
    await screen.findByLabelText("Filter settings");
    await userEvent.click(screen.getByRole("button", { name: "Expand all" }));
    expect(await screen.findByText("Queue concurrency")).toBeInTheDocument();
    expect(screen.getByText("Recovery attempts")).toBeInTheDocument();
  });

  it("one click applies the backend's first model when stale", async () => {
    const fetchMock = makeFetchMock({ default_model: "gpt-wrong" }, ["gpt-a", "gpt-b"]);
    vi.stubGlobal("fetch", fetchMock);
    render(<Settings />);
    await expand(/Agent defaults/);
    await userEvent.click(await screen.findByText(/Use gpt-a/));
    await waitFor(() => {
      const postCall = fetchMock.mock.calls.find(
        ([url, init]) => String(url).includes("/api/settings") && init?.method === "POST"
      );
      expect(postCall).toBeTruthy();
      const body = JSON.parse((postCall?.[1] as RequestInit).body as string) as {
        key: string;
        value: string;
      };
      expect(body.key).toBe("default_model");
      expect(body.value).toBe("gpt-a");
    });
  });

  it("reverts the draft when a save fails", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (String(url).includes("/api/settings") && init?.method === "POST") {
          const body = JSON.parse((init.body as string) || "{}") as Record<string, unknown>;
          if (body.key === "concurrency") {
            return { ok: false, status: 400, json: async () => ({ error: "bad value" }) };
          }
        }
        return (fetchMock as unknown as (u: string, i?: RequestInit) => Promise<unknown>)(
          url,
          init
        );
      })
    );
    render(<Settings />);
    await expand(/Queue & timeouts/);
    const input = screen.getByRole("spinbutton", { name: "Queue concurrency" });
    await userEvent.clear(input);
    await userEvent.type(input, "9");
    await userEvent.tab();
    await waitFor(() => expect(input).toHaveValue(4));

    expect(await screen.findAllByText("bad value")).toHaveLength(2);
  });

  it("restore previews the backup then executes on RESTORE", async () => {
    localStorage.setItem("jalebi-settings-show-advanced-v1", "1");
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Settings />);
    await expand(/Data management/);
    await userEvent.click(await screen.findByText("restore"));
    expect(await screen.findByText("Integrity check:")).toBeInTheDocument();
    expect(screen.getByText("passed", { exact: false })).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Type RESTORE to confirm restore"), "RESTORE");
    await userEvent.click(screen.getByRole("button", { name: "Restore now" }));
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) =>
            String(url).includes("/restore") &&
            init?.method === "POST" &&
            String(init.body).includes("RESTORE")
        )
      ).toBe(true);
    });
    expect(await screen.findByText(/restored .* safety snapshot/)).toBeInTheDocument();
  });

  it("restore stays disabled while screening runs are busy", async () => {
    localStorage.setItem("jalebi-settings-show-advanced-v1", "1");
    const base = makeFetchMock();
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url).includes("/restore")) {
        return {
          ok: true,
          json: async () => ({
            dry_run: true,
            preview: {
              backup: "data-20260101-000000.db",
              integrity_ok: true,
              integrity_detail: null,
              busy_tasks: 0,
              busy_screenings: 2,
            },
          }),
        };
      }
      return base(url, init);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<Settings />);
    await expand(/Data management/);
    await userEvent.click(await screen.findByText("restore"));
    expect(await screen.findByText(/2 screening run\(s\).*wait/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restore now" })).toBeDisabled();
  });

  it("unchecking a backend saves the reduced enabled list", async () => {    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Settings />);
    await expand(/Agent defaults/);
    expect(await screen.findByText("Active backends")).toBeInTheDocument();
    const section = screen.getByRole("heading", { name: "Active backends" }).closest("section")!;
    await userEvent.click(within(section).getByRole("checkbox", { name: "claude" }));
    await waitFor(() => {
      const bodies = fetchMock.mock.calls
        .filter(([url, init]) => String(url).includes("/api/settings") && init?.method === "POST")
        .map(([, init]) => JSON.parse((init?.body as string) || "{}"));
      expect(
        bodies.some(
          (b) =>
            b.key === "enabled_backends" &&
            JSON.stringify(b.value) === JSON.stringify(["opencode", "codex"])
        )
      ).toBe(true);
    });
  });

  it("shows the active backends notice with link to GitHub issues", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Settings />);
    await expand(/Agent defaults/);
    expect(screen.queryByText(/haven't been tested from the app yet/)).not.toBeInTheDocument();
    expect(screen.queryByText(/not yet tested from the app/)).not.toBeInTheDocument();

    const link = await screen.findByRole("link", { name: /^here$/i });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute("href", "https://github.com/samosa-ai-com/Jalebi/issues/new");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer");
    expect(
      screen.getByText(/Backend versions change over time/)
    ).toBeInTheDocument();
  });

  it("timezone dropdown saves the chosen zone", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Settings />);
    await expand(/Queue & timeouts/);
    await userEvent.click(await screen.findByRole("button", { name: "Timezone" }));
    await userEvent.type(screen.getByRole("combobox"), "Kolkata");
    await userEvent.click(screen.getByRole("option", { name: /Asia\/Kolkata/ }));
    await waitFor(() => {
      const bodies = fetchMock.mock.calls
        .filter(([url, init]) => String(url).includes("/api/settings") && init?.method === "POST")
        .map(([, init]) => JSON.parse((init?.body as string) || "{}"));
      expect(bodies.some((b) => b.key === "timezone" && b.value === "Asia/Kolkata")).toBe(true);
    });
  });

  it("explains where each backend's model list comes from", async () => {
    localStorage.setItem("jalebi-settings-show-advanced-v1", "1");
    vi.stubGlobal("fetch", makeFetchMock());
    render(<Settings />);
    await expand(/Agent defaults/);
    expect(await screen.findByText(/Where each built-in list comes from/)).toBeInTheDocument();
    expect(screen.getByText(/Fixed alias list in the app/)).toBeInTheDocument();
    expect(screen.getByText(/re-run `codex login` to refresh/)).toBeInTheDocument();
  });

  it("ticking a secret preset saves the combined pattern list", async () => {
    localStorage.setItem("jalebi-settings-show-advanced-v1", "1");
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Settings />);
    await expand(/Queue & timeouts/);
    await userEvent.click(await screen.findByRole("checkbox", { name: "AWS access keys" }));
    await waitFor(() => {
      const bodies = fetchMock.mock.calls
        .filter(([url, init]) => String(url).includes("/api/settings") && init?.method === "POST")
        .map(([, init]) => JSON.parse((init?.body as string) || "{}"));
      expect(
        bodies.some((b) => b.key === "secret_patterns" && b.value.includes("AKIA[0-9A-Z]{16}"))
      ).toBe(true);
    });
  });

  it("applies focus-within stacking lift to Row sections", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    render(<Settings />);
    await expand(/Agent defaults/);
    const heading = await screen.findByText("Default backend");
    const rowSection = heading.closest("section");
    expect(rowSection?.className).toMatch(/\bfocus-within:relative\b/);
    expect(rowSection?.className).toMatch(/\bfocus-within:z-30\b/);
    // The lift also engages while a dropdown reports open (aria-expanded),
    // so focus loss with an open list can't drop the row behind its siblings.
    expect(rowSection?.className).toContain('has-[[aria-expanded="true"]]:relative');
    expect(rowSection?.className).toContain('has-[[aria-expanded="true"]]:z-30');
  });

  it("renders normalized Non-retryable errors textarea with full width and min height", async () => {
    localStorage.setItem("jalebi-settings-show-advanced-v1", "1");
    vi.stubGlobal("fetch", makeFetchMock());
    render(<Settings />);
    await expand(/Recovery/);
    const textarea = await screen.findByLabelText("Non-retryable errors");
    expect(textarea.className).toMatch(/\bw-full\b/);
    expect(textarea.className).toMatch(/\bmax-w-md\b/);
    expect(textarea.className).toMatch(/\bmin-h-24\b/);
  });

  it("renders normalized Model overrides help box with relaxed leading and vertical rhythm", async () => {
    localStorage.setItem("jalebi-settings-show-advanced-v1", "1");
    vi.stubGlobal("fetch", makeFetchMock());
    render(<Settings />);
    await expand(/Agent defaults/);
    const helpTitle = await screen.findByText(/Where each built-in list comes from/);
    const container = helpTitle.closest("div");
    expect(container?.className).toMatch(/\bspace-y-1.5\b/);
    expect(container?.className).toMatch(/\bpt-3\b/);
    const helpItem = screen.getByText(/Live: `opencode models`/);
    expect(helpItem.closest("li")?.className).toMatch(/\bleading-relaxed\b/);
  });

  it("renders the expanded Auto-nudge help text", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    render(<Settings />);
    await expand(/Recovery/);
    expect(await screen.findByText("Auto-nudge")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Watches your tasks’ pull requests for new signals — CI failures (via commit-status webhooks, or the GitHub poller when webhooks aren’t configured) and change-requested reviews (via the poller). When one arrives, it automatically queues a follow-up on the same agent session with the failing check or review as context, so the PR-feedback loop resolves without babysitting. Guardrails: only tasks waiting on attention with a resumable session qualify — never queued, running, done, cancelled, or interrupted ones; one nudge per unique signal, max 3 nudges per task."
      )
    ).toBeInTheDocument();
  });

  it("renders the Browser notifications toggle card with description and toggle action", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    const mockRequestPermission = vi.fn().mockResolvedValue("granted");
    class MockNotification {
      static permission: NotificationPermission = "default";
      static requestPermission = mockRequestPermission;
    }
    vi.stubGlobal("Notification", MockNotification);

    render(<Settings />);
    await expand(/Notifications/);
    expect(await screen.findByText("Browser notifications")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Push notification when a task completes, fails, or needs approval while Jalebi is in the background."
      )
    ).toBeInTheDocument();

    const switchBtn = screen.getByRole("switch", { name: "Browser notifications" });
    expect(switchBtn).toHaveAttribute("aria-checked", "false");

    await userEvent.click(switchBtn);
    expect(mockRequestPermission).toHaveBeenCalled();
    await waitFor(() => {
      expect(switchBtn).toHaveAttribute("aria-checked", "true");
      expect(localStorage.getItem("jalebi-browser-notifications-v1")).toBe("1");
    });

    await userEvent.click(switchBtn);
    await waitFor(() => {
      expect(switchBtn).toHaveAttribute("aria-checked", "false");
      expect(localStorage.getItem("jalebi-browser-notifications-v1")).toBe("0");
    });
  });

  it("indicates blocked permission when browser notifications permission is denied", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    class MockNotification {
      static permission: NotificationPermission = "denied";
      static requestPermission = vi.fn().mockResolvedValue("denied");
    }
    vi.stubGlobal("Notification", MockNotification);

    render(<Settings />);
    await expand(/Notifications/);
    const hint = await screen.findByText("Permission blocked in browser settings");
    expect(hint).toBeInTheDocument();
    expect(hint).toHaveAttribute("id", "browser-notifications-hint");
    const switchBtn = screen.getByRole("switch", { name: "Browser notifications" });
    expect(switchBtn).toBeDisabled();
    expect(switchBtn).toHaveAttribute("aria-describedby", "browser-notifications-hint");
  });

  it("hides advanced settings by default and reveals them when Advanced toggle is clicked", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    render(<Settings />);

    const advToggle = await screen.findByRole("switch", { name: "Show advanced settings" });
    expect(advToggle).toHaveAttribute("aria-checked", "false");

    await expand(/Queue & timeouts/);
    expect(screen.getByText("Queue concurrency")).toBeInTheDocument();
    expect(screen.queryByText("Timeout")).not.toBeInTheDocument();
    expect(screen.queryByText("Stall timeout")).not.toBeInTheDocument();

    // Toggle advanced ON
    await userEvent.click(advToggle);
    expect(advToggle).toHaveAttribute("aria-checked", "true");
    expect(localStorage.getItem("jalebi-settings-show-advanced-v1")).toBe("1");

    expect(await screen.findByText("Timeout")).toBeInTheDocument();
    expect(screen.getByText("Stall timeout")).toBeInTheDocument();
  });

  it("shows hint bar when search query matches hidden advanced settings", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    render(<Settings />);

    const searchInput = await screen.findByLabelText("Filter settings");
    await userEvent.type(searchInput, "stall timeout");

    expect(
      await screen.findByText(/matching setting\(s\) are advanced and hidden\./)
    ).toBeInTheDocument();
    const showAdvBtn = screen.getByRole("button", { name: "Show advanced" });
    expect(showAdvBtn).toBeInTheDocument();

    await userEvent.click(showAdvBtn);
    expect(await screen.findByText("Stall timeout")).toBeInTheDocument();
    expect(screen.queryByText(/matching setting\(s\) are advanced and hidden\./)).not.toBeInTheDocument();
  });

  it("deep link forces hidden advanced section open even when Advanced is off", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    window.history.pushState({}, "", "/settings?section=webhooks");
    try {
      render(<Settings />);
      expect(await screen.findByText("Webhooks")).toBeInTheDocument();
      expect(screen.getByText("Public webhook URL (tunnel base)")).toBeInTheDocument();
    } finally {
      window.history.pushState({}, "", "/settings");
    }
  });

  it("search browser with toggle OFF reveals the Browser card without being silently absent", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    render(<Settings />);

    const searchInput = await screen.findByLabelText("Filter settings");
    await userEvent.type(searchInput, "browser");

    expect(await screen.findByText("Browser notifications")).toBeInTheDocument();
    expect(screen.queryByText(/matching setting\(s\) are advanced and hidden\./)).not.toBeInTheDocument();
  });

  it("search still running with toggle OFF shows hint bar then reveals after click", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    render(<Settings />);

    const searchInput = await screen.findByLabelText("Filter settings");
    await userEvent.type(searchInput, "still running");

    expect(
      await screen.findByText(/matching setting\(s\) are advanced and hidden\./)
    ).toBeInTheDocument();
    const showAdvBtn = screen.getByRole("button", { name: "Show advanced" });
    expect(showAdvBtn).toBeInTheDocument();

    await userEvent.click(showAdvBtn);
    expect(await screen.findByText("Still running (interval pings)")).toBeInTheDocument();
    expect(screen.queryByText(/matching setting\(s\) are advanced and hidden\./)).not.toBeInTheDocument();
  });

  it("search prune with toggle OFF shows hint then reveals", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    render(<Settings />);

    const searchInput = await screen.findByLabelText("Filter settings");
    await userEvent.type(searchInput, "prune");

    expect(
      await screen.findByText(/matching setting\(s\) are advanced and hidden\./)
    ).toBeInTheDocument();
    const showAdvBtn = screen.getByRole("button", { name: "Show advanced" });
    expect(showAdvBtn).toBeInTheDocument();

    await userEvent.click(showAdvBtn);
    expect(await screen.findByText("Clean up old data")).toBeInTheDocument();
    expect(screen.queryByText(/matching setting\(s\) are advanced and hidden\./)).not.toBeInTheDocument();
  });

  it("search import reveals the env import form", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    render(<Settings />);

    const searchInput = await screen.findByLabelText("Filter settings");
    await userEvent.type(searchInput, "import");

    expect(
      await screen.findByText(/matching setting\(s\) are advanced and hidden\./)
    ).toBeInTheDocument();
    const showAdvBtn = screen.getByRole("button", { name: "Show advanced" });
    expect(showAdvBtn).toBeInTheDocument();

    await userEvent.click(showAdvBtn);
    expect(await screen.findByPlaceholderText(/Paste a \.env file/)).toBeInTheDocument();
    expect(screen.queryByText(/matching setting\(s\) are advanced and hidden\./)).not.toBeInTheDocument();
  });
});

