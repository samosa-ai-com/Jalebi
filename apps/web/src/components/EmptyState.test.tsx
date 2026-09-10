import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { EmptyState } from "./EmptyState";

describe("EmptyState", () => {
  it("renders title and description", () => {
    render(
      <EmptyState
        title="No items found"
        description="There are currently no items available to display."
      />
    );
    expect(screen.getByRole("heading", { level: 3, name: "No items found" })).toBeInTheDocument();
    expect(
      screen.getByText("There are currently no items available to display.")
    ).toBeInTheDocument();
  });

  it("renders icon when provided", () => {
    render(
      <EmptyState
        icon={<span data-testid="custom-icon">✨</span>}
        title="Empty"
        description="Nothing here"
      />
    );
    expect(screen.getByTestId("custom-icon")).toBeInTheDocument();
  });

  it("renders action button and handles clicks", async () => {
    const handleClick = vi.fn();
    render(
      <EmptyState
        title="Empty"
        description="Nothing here"
        action={
          <button type="button" onClick={handleClick}>
            Create new
          </button>
        }
      />
    );

    const button = screen.getByRole("button", { name: "Create new" });
    expect(button).toBeInTheDocument();
    await userEvent.click(button);
    expect(handleClick).toHaveBeenCalledTimes(1);
  });
});
