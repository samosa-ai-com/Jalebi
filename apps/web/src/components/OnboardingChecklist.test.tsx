import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import OnboardingChecklist, { ONBOARDING_DISMISSED_KEY } from "./OnboardingChecklist";

describe("OnboardingChecklist", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("shows all three steps and correct done states for given counts", async () => {
    const onStartTask = vi.fn();
    const { rerender } = render(
      <MemoryRouter>
        <OnboardingChecklist accounts={0} repos={0} tasks={0} onStartTask={onStartTask} />
      </MemoryRouter>
    );

    expect(screen.getByText("Setup")).toBeInTheDocument();
    expect(screen.getByText("0 of 3 done")).toBeInTheDocument();

    const stepAccount = screen.getByTestId("step-account");
    const stepRepo = screen.getByTestId("step-repo");
    const stepTask = screen.getByTestId("step-task");

    expect(stepAccount).toHaveAttribute("data-done", "false");
    expect(stepRepo).toHaveAttribute("data-done", "false");
    expect(stepTask).toHaveAttribute("data-done", "false");

    const githubLink = within(stepAccount).getByRole("link", { name: "Add your GitHub account" });
    expect(githubLink).toHaveAttribute("href", "/github");

    const repoLink = within(stepRepo).getByRole("link", { name: "Connect a repository" });
    expect(repoLink).toHaveAttribute("href", "/repos");

    const taskBtn = within(stepTask).getByRole("button", { name: "Create your first task" });
    await userEvent.click(taskBtn);
    expect(onStartTask).toHaveBeenCalledTimes(1);

    // Rerender with 1 account connected
    rerender(
      <MemoryRouter>
        <OnboardingChecklist accounts={1} repos={0} tasks={0} onStartTask={onStartTask} />
      </MemoryRouter>
    );
    expect(screen.getByText("1 of 3 done")).toBeInTheDocument();
    expect(screen.getByTestId("step-account")).toHaveAttribute("data-done", "true");
    expect(within(screen.getByTestId("step-account")).getByText("✓")).toBeInTheDocument();
    expect(screen.getByTestId("step-repo")).toHaveAttribute("data-done", "false");
    expect(screen.getByTestId("step-task")).toHaveAttribute("data-done", "false");

    // Rerender with accounts and repos connected
    rerender(
      <MemoryRouter>
        <OnboardingChecklist accounts={2} repos={1} tasks={0} onStartTask={onStartTask} />
      </MemoryRouter>
    );
    expect(screen.getByText("2 of 3 done")).toBeInTheDocument();
    expect(screen.getByTestId("step-account")).toHaveAttribute("data-done", "true");
    expect(screen.getByTestId("step-repo")).toHaveAttribute("data-done", "true");
    expect(within(screen.getByTestId("step-repo")).getByText("✓")).toBeInTheDocument();
    expect(screen.getByTestId("step-task")).toHaveAttribute("data-done", "false");
  });

  it("returns null when all three steps are complete", () => {
    const { container } = render(
      <MemoryRouter>
        <OnboardingChecklist accounts={1} repos={1} tasks={1} />
      </MemoryRouter>
    );
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText("Setup")).not.toBeInTheDocument();
  });

  it("dismiss hides the checklist and persists to localStorage", async () => {
    render(
      <MemoryRouter>
        <OnboardingChecklist accounts={0} repos={0} tasks={0} />
      </MemoryRouter>
    );

    expect(screen.getByText("Setup")).toBeInTheDocument();
    const dismissBtn = screen.getByRole("button", { name: /dismiss/i });
    await userEvent.click(dismissBtn);

    expect(screen.queryByText("Setup")).not.toBeInTheDocument();
    expect(localStorage.getItem(ONBOARDING_DISMISSED_KEY)).toBe("1");
  });

  it("returns null on initial render if already dismissed in localStorage", () => {
    localStorage.setItem(ONBOARDING_DISMISSED_KEY, "1");
    const { container } = render(
      <MemoryRouter>
        <OnboardingChecklist accounts={0} repos={0} tasks={0} />
      </MemoryRouter>
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("satisfies target size contract with min-h-6 on dismiss button", () => {
    render(
      <MemoryRouter>
        <OnboardingChecklist accounts={0} repos={0} tasks={0} />
      </MemoryRouter>
    );
    const dismissBtn = screen.getByRole("button", { name: /dismiss/i });
    expect(dismissBtn.className).toMatch(/\bmin-h-6\b/);
  });
});
