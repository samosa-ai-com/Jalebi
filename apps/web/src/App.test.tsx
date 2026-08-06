import { render, screen, within } from "@testing-library/react";
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
});
