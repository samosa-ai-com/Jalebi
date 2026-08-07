import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Settings from "./Settings";

const SETTINGS = {
  concurrency: 4,
  auto_publish: true,
  ntfy_topic: "",
  ntfy_url: "",
  default_timeout_minutes: 30,
  retry_policy: { auto_retry: false },
  secret_patterns: [],
  artifact_ttl_days: 7,
  agent_cli: "opencode",
};

describe("Settings", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads settings and toggles auto_publish via POST", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("/api/settings") && init?.method === "POST") {
        const body = JSON.parse(init.body as string) as Record<string, unknown>;
        return { ok: true, json: async () => ({ [String(body.key)]: body.value }) };
      }
      return { ok: true, json: async () => SETTINGS };
    });
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
});
