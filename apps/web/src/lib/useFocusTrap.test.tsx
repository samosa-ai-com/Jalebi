import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { useFocusTrap } from "./useFocusTrap";

function TestModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const trapRef = useFocusTrap<HTMLDivElement>(open);
  if (!open) return null;

  return (
    <div ref={trapRef} role="dialog" aria-modal="true">
      <h2>Modal Title</h2>
      <button type="button" data-testid="first-btn">
        First
      </button>
      <input data-testid="modal-input" placeholder="Input" />
      <button type="button" data-testid="last-btn" onClick={onClose}>
        Last
      </button>
    </div>
  );
}

function TestApp() {
  const [open, setOpen] = useState(false);

  return (
    <div>
      <button
        type="button"
        data-testid="opener-btn"
        onClick={() => setOpen(true)}
      >
        Open Modal
      </button>
      <TestModal open={open} onClose={() => setOpen(false)} />
    </div>
  );
}

describe("useFocusTrap", () => {
  it("focuses the first focusable element when opened", async () => {
    render(<TestApp />);
    const opener = screen.getByTestId("opener-btn");
    opener.focus();
    expect(document.activeElement).toBe(opener);

    await userEvent.click(opener);

    const firstBtn = screen.getByTestId("first-btn");
    expect(document.activeElement).toBe(firstBtn);
  });

  it("cycles Tab from last element back to first element", async () => {
    render(<TestApp />);
    const opener = screen.getByTestId("opener-btn");
    await userEvent.click(opener);

    const firstBtn = screen.getByTestId("first-btn");
    const lastBtn = screen.getByTestId("last-btn");

    lastBtn.focus();
    expect(document.activeElement).toBe(lastBtn);

    // Press Tab on last button -> cycles to first
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(firstBtn);
  });

  it("cycles Shift+Tab from first element to last element", async () => {
    render(<TestApp />);
    const opener = screen.getByTestId("opener-btn");
    await userEvent.click(opener);

    const firstBtn = screen.getByTestId("first-btn");
    const lastBtn = screen.getByTestId("last-btn");

    firstBtn.focus();
    expect(document.activeElement).toBe(firstBtn);

    // Press Shift+Tab on first button -> cycles to last
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(lastBtn);
  });

  it("returns focus to the opener element when closed", async () => {
    render(<TestApp />);
    const opener = screen.getByTestId("opener-btn");
    opener.focus();
    await userEvent.click(opener);

    expect(screen.getByRole("dialog")).toBeInTheDocument();

    const lastBtn = screen.getByTestId("last-btn");
    await userEvent.click(lastBtn);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.activeElement).toBe(opener);
  });

  it("falls back to the container when every control is disabled (busy state)", async () => {
    function BusyModal({ open }: { open: boolean }) {
      const trapRef = useFocusTrap<HTMLDivElement>(open);
      if (!open) return null;
      return (
        <div ref={trapRef} role="dialog" aria-modal="true" tabIndex={-1}>
          <button type="button" disabled>
            Confirm
          </button>
          <button type="button" disabled>
            Cancel
          </button>
        </div>
      );
    }
    function BusyApp() {
      const [open, setOpen] = useState(false);
      return (
        <div>
          <button type="button" data-testid="busy-opener" onClick={() => setOpen(true)}>
            Open
          </button>
          <BusyModal open={open} />
        </div>
      );
    }
    render(<BusyApp />);
    await userEvent.click(screen.getByTestId("busy-opener"));
    // No focusable descendants: focus must land on the focusable container
    // so Tab stays inside the modal instead of escaping to the page.
    expect(document.activeElement).toBe(screen.getByRole("dialog"));
  });
});
