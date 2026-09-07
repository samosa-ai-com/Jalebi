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
    const backendSelect = within(section).getByRole("combobox") as HTMLSelectElement;

    expect(backendSelect.value).toBe("opencode");
    expect([...backendSelect.options].map((o) => o.value)).toEqual(
      expect.arrayContaining(["opencode", "codex", "claude"])
    );
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
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Settings />);
    await expand(/Recovery/);
    expect(await screen.findByText("Recovery attempts")).toBeInTheDocument();
    expect(screen.getByText("Non-retryable errors")).toBeInTheDocument();

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
      await screen.findByText(/isn't in this backend's list/, { exact: false })
    ).toBeInTheDocument();
  });

  it("renders the Data management section with storage and prune", async () => {
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
      const bodies = fetchMock.mock.calls
        .filter(([url, init]) => String(url).includes("/api/settings") && init?.method === "POST")
        .map(([, init]) => JSON.parse((init?.body as string) || "{}"));
      expect(bodies.some((b) => b.key === "default_model" && b.value === "gpt-a")).toBe(true);
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

  it("unchecking a backend saves the reduced enabled list", async () => {
    const fetchMock = makeFetchMock();
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

  it("timezone dropdown saves the chosen zone", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Settings />);
    await expand(/Queue & timeouts/);
    const select = await screen.findByRole("combobox", { name: "Timezone" });
    await userEvent.selectOptions(select, "Asia/Kolkata");
    await waitFor(() => {
      const bodies = fetchMock.mock.calls
        .filter(([url, init]) => String(url).includes("/api/settings") && init?.method === "POST")
        .map(([, init]) => JSON.parse((init?.body as string) || "{}"));
      expect(bodies.some((b) => b.key === "timezone" && b.value === "Asia/Kolkata")).toBe(true);
    });
  });

  it("ticking a secret preset saves the combined pattern list", async () => {
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
});
