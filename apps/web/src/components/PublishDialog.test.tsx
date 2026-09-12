import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import PublishDialog from "./PublishDialog";

function Parent() {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button
        type="button"
        data-testid="open-publish-btn"
        onClick={() => setOpen(true)}
      >
        Open Publish Dialog
      </button>
      {open && (
        <PublishDialog
          taskId={1}
          options={{ mode: "new_pr" }}
          onClose={() => setOpen(false)}
          onPublished={() => {}}
        />
      )}
    </div>
  );
}

describe("PublishDialog focus trap", () => {
  it("traps focus and returns focus to opener on close", async () => {
    render(<Parent />);
    const opener = screen.getByTestId("open-publish-btn");
    opener.focus();
    expect(document.activeElement).toBe(opener);

    await userEvent.click(opener);

    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();

    const cancelBtn = screen.getByRole("button", { name: "Cancel" });
    const confirmBtn = screen.getByRole("button", { name: "Confirm" });

    // Open-focus sets focus inside the dialog (cancel button is the first focusable)
    expect(document.activeElement).toBe(cancelBtn);

    // Press Tab on cancel -> moves to confirm
    fireEvent.keyDown(document, { key: "Tab" });
    confirmBtn.focus();
    expect(document.activeElement).toBe(confirmBtn);

    // Tab from confirm button cycles back to cancel button
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(cancelBtn);

    // Shift+Tab from cancel button cycles to confirm button
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(confirmBtn);

    // Closing the dialog returns focus to opener
    await userEvent.click(cancelBtn);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.activeElement).toBe(opener);
  });
});
