import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import NextSteps from "./NextSteps";
import { getNextSteps } from "../lib/nextSteps";

describe("getNextSteps", () => {
  it("points a done task without a PR at publishing", () => {
    expect(
      getNextSteps({ type: "freeform", status: "done", repo_full_name: "o/r" })
    ).toEqual([{ label: "Publish to open a pull request", targetId: "publish-actions" }]);
  });

  it("points needs_approval at review-then-publish", () => {
    expect(getNextSteps({ type: "issue_fix", status: "needs_approval" })).toEqual([
      { label: "Review, then publish", targetId: "publish-actions" },
    ]);
    expect(
      getNextSteps({ type: "issue_fix", status: "needs_approval", canFollowUp: true })
    ).toEqual([
      { label: "Review, then publish", targetId: "publish-actions" },
      { label: "Send a follow-up on this task", targetId: "followup-composer" },
    ]);
  });

  it("links a done task with a PR to GitHub plus a follow-up", () => {
    expect(
      getNextSteps({
        type: "issue_fix",
        status: "done",
        pr_number: 7,
        repo_full_name: "o/r",
        canFollowUp: true,
      })
    ).toEqual([
      { label: "Open pull request #7 on GitHub", href: "https://github.com/o/r/pull/7" },
      { label: "Send a follow-up on this task", targetId: "followup-composer" },
    ]);
  });

  it("omits the follow-up step when no resumable session exists", () => {
    expect(
      getNextSteps({
        type: "issue_fix",
        status: "done",
        pr_number: 7,
        repo_full_name: "o/r",
        canFollowUp: false,
      })
    ).toEqual([
      { label: "Open pull request #7 on GitHub", href: "https://github.com/o/r/pull/7" },
    ]);
  });

  it("points failed runs at re-run and follow-up", () => {
    expect(
      getNextSteps({ type: "freeform", status: "failed", canFollowUp: true })
    ).toEqual([
      { label: "Re-run this task", targetId: "publish-actions" },
      { label: "Send a follow-up with more context", targetId: "followup-composer" },
    ]);
  });

  it("points a done review at the posted review", () => {
    expect(
      getNextSteps({ type: "pr_review", status: "done", prs: [3], repo_full_name: "o/r" })
    ).toEqual([
      { label: "View the posted review on PR #3", href: "https://github.com/o/r/pull/3" },
    ]);
  });

  it("returns nothing for a review with no linked PR", () => {
    expect(getNextSteps({ type: "pr_review", status: "done" })).toEqual([]);
    expect(
      getNextSteps({ type: "pr_review", status: "done", prs: [3] })
    ).toEqual([]);
  });

  it("returns nothing for running or queued tasks", () => {
    expect(getNextSteps({ type: "freeform", status: "running" })).toEqual([]);
    expect(getNextSteps({ type: "freeform", status: "queued" })).toEqual([]);
  });
});

describe("NextSteps", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders nothing when there are no steps", () => {
    const { container } = render(<NextSteps steps={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders numbered steps with min-h-6 targets", () => {
    render(
      <NextSteps
        steps={[
          { label: "Publish to open a pull request", targetId: "publish-actions" },
          { label: "Open pull request #7 on GitHub", href: "https://example.org/pr/7" },
        ]}
      />
    );
    expect(screen.getByRole("heading", { name: "What next" })).toBeInTheDocument();
    const card = screen.getByRole("region", { name: "What next" });
    expect(within(card).getByRole("button", { name: "Publish to open a pull request" })).toBeInTheDocument();
    const link = within(card).getByRole("link", { name: /Open pull request #7/ });
    expect(link).toHaveAttribute("href", "https://example.org/pr/7");
    for (const el of [
      ...within(card).getAllByRole("button"),
      ...within(card).getAllByRole("link"),
    ]) {
      expect(el.className).toMatch(/\bmin-h-6\b/);
    }
  });

  it("scrolls to the target section on click", async () => {
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    document.body.innerHTML = '<div id="publish-actions"></div>';
    render(
      <NextSteps steps={[{ label: "Publish to open a pull request", targetId: "publish-actions" }]} />
    );
    await userEvent.click(screen.getByRole("button", { name: "Publish to open a pull request" }));
    expect(window.HTMLElement.prototype.scrollIntoView).toHaveBeenCalledWith({
      behavior: "smooth",
      block: "start",
    });
    document.body.innerHTML = "";
  });
});
