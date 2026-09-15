import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import BackendMissingDialog from "./BackendMissingDialog";

describe("BackendMissingDialog", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("offers installed backends and applies the pick, then closes", async () => {
    const onPick = vi.fn();
    const onClose = vi.fn();
    render(
      <MemoryRouter>
        <BackendMissingDialog missing="codex" installed={["opencode", "kilo"]} onPick={onPick} onClose={onClose} />
      </MemoryRouter>
    );
    expect(screen.getByRole("dialog", { name: "Backend not installed" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Use kilo" }));
    expect(onPick).toHaveBeenCalledWith("kilo");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("shows a Settings path when nothing is installed", () => {
    render(
      <MemoryRouter>
        <BackendMissingDialog missing="codex" installed={[]} onPick={vi.fn()} onClose={vi.fn()} />
      </MemoryRouter>
    );
    expect(screen.queryByRole("button", { name: /Use / })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Settings" })).toHaveAttribute(
      "href",
      "/settings?section=agent"
    );
  });

  it("closes on Escape", async () => {
    const onClose = vi.fn();
    render(
      <MemoryRouter>
        <BackendMissingDialog missing="codex" installed={["opencode"]} onPick={vi.fn()} onClose={onClose} />
      </MemoryRouter>
    );
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
