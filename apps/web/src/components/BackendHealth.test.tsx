import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import BackendHealth from "./BackendHealth";

describe("BackendHealth", () => {
  it("lists install state and flags version drift", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Promise.resolve({
          ok: true,
          json: async () => ({
            backends: [
              {
                cli: "opencode",
                installed: true,
                version: "1.18.30",
                verified: "1.18",
                version_match: true,
              },
              {
                cli: "codex",
                installed: true,
                version: "0.153.4",
                verified: "0.147.0",
                version_match: false,
              },
              {
                cli: "grok",
                installed: false,
                version: null,
                verified: "1.0.13",
                version_match: false,
              },
            ],
          }),
        })
      )
    );
    render(<BackendHealth />);
    expect(await screen.findByText("opencode")).toBeInTheDocument();
    expect(screen.getByText("1.18.30")).toBeInTheDocument();
    expect(screen.getByText("not installed")).toBeInTheDocument();
    expect(screen.getByText(/differs from verified 0.147.0/)).toBeInTheDocument();
  });

  it("degrades when the endpoint fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Promise.reject(new Error("down")))
    );
    render(<BackendHealth />);
    expect(await screen.findByText("Backend health unavailable.")).toBeInTheDocument();
  });
});
