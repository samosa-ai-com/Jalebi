import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

describe("App", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the app title and shows the server status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ status: "ok" }),
      })
    );

    render(<App />);
    expect(screen.getByText("Jalebi")).toBeInTheDocument();
    expect(await screen.findByText("Server: ok")).toBeInTheDocument();
  });

  it("shows an error when the server is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Network error")));

    render(<App />);
    expect(await screen.findByText("Server unreachable: Network error")).toBeInTheDocument();
  });
});
