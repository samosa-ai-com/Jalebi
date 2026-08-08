import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Settings from "./Settings";

const SETTINGS = {
  concurrency: 4,
  auto_publish: true,
  ntfy_topic: "",
  default_timeout_minutes: 60,
  retry_policy: { auto_retry: false },
  secret_patterns: [],
  artifact_ttl_days: 7,
  agent_cli: "opencode",
  notify_on_done: true,
  notify_on_failed: true,
  notify_on_progress: true,
  notify_on_needs_approval: true,
  notify_progress_interval_minutes: 30,
  webhook_url: "",
  webhook_secret: "",
};

function makeFetchMock() {
  return vi.fn(async (url: string, init?: RequestInit) => {
    if (String(url).includes("/api/notify/test")) {
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
    return { ok: true, json: async () => SETTINGS };
  });
}

describe("Settings", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads settings and toggles auto_publish via POST", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    render(<Settings />);
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
    expect(await screen.findByText("Timeout")).toBeInTheDocument();
    expect(screen.queryByText("Default timeout")).not.toBeInTheDocument();
  });
});
