import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import OnboardingChecklist, {
  ONBOARDING_DISMISSED_KEY,
  ONBOARDING_MANUAL_DONE_KEY,
} from "./OnboardingChecklist";

describe("OnboardingChecklist", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("shows all five steps and correct done states for given counts", async () => {
    const onStartTask = vi.fn();
    const { rerender } = render(
      <MemoryRouter>
        <OnboardingChecklist accounts={0} repos={0} tasks={0} onStartTask={onStartTask} />
      </MemoryRouter>
    );

    expect(screen.getByText("Setup")).toBeInTheDocument();
    expect(screen.getByText("0 of 5 done")).toBeInTheDocument();

    const stepAccount = screen.getByTestId("step-account");
    const stepRepo = screen.getByTestId("step-repo");
    const stepTask = screen.getByTestId("step-task");
    const stepBackend = screen.getByTestId("step-backend");
    const stepPr = screen.getByTestId("step-pr");

    for (const step of [stepAccount, stepRepo, stepTask, stepBackend, stepPr]) {
      expect(step).toHaveAttribute("data-done", "false");
    }

    const githubLink = within(stepAccount).getByRole("link", { name: "Add your GitHub account" });
    expect(githubLink).toHaveAttribute("href", "/github");

    const repoLink = within(stepRepo).getByRole("link", { name: "Connect a repository" });
    expect(repoLink).toHaveAttribute("href", "/repos");

    const taskBtn = within(stepTask).getByRole("button", { name: "Create your first task" });
    await userEvent.click(taskBtn);
    expect(onStartTask).toHaveBeenCalledTimes(1);

    const backendLink = within(stepBackend).getByRole("link", {
      name: "Choose your AI backend & model",
    });
    expect(backendLink).toHaveAttribute("href", "/settings?section=agent");

    // Backend comes before the first task: a task can only succeed with a working backend.
    const items = screen.getAllByRole("listitem");
    const ids = items.map((li) => li.getAttribute("data-testid"));
    expect(ids).toEqual(["step-account", "step-repo", "step-backend", "step-task", "step-pr"]);

    // Rerender with 1 account connected
    rerender(
      <MemoryRouter>
        <OnboardingChecklist accounts={1} repos={0} tasks={0} onStartTask={onStartTask} />
      </MemoryRouter>
    );
    expect(screen.getByText("1 of 5 done")).toBeInTheDocument();
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
    expect(screen.getByText("2 of 5 done")).toBeInTheDocument();
    expect(screen.getByTestId("step-account")).toHaveAttribute("data-done", "true");
    expect(screen.getByTestId("step-repo")).toHaveAttribute("data-done", "true");
    expect(within(screen.getByTestId("step-repo")).getByText("✓")).toBeInTheDocument();
    expect(screen.getByTestId("step-task")).toHaveAttribute("data-done", "false");

    // Backend + PR props complete the remaining steps.
    rerender(
      <MemoryRouter>
        <OnboardingChecklist
          accounts={2}
          repos={1}
          tasks={1}
          hasModelChoice
          hasPublishedPr
          onStartTask={onStartTask}
        />
      </MemoryRouter>
    );
    expect(screen.queryByText("Setup")).not.toBeInTheDocument();
  });

  it("returns null when all five steps are complete", () => {
    const { container } = render(
      <MemoryRouter>
        <OnboardingChecklist
          accounts={1}
          repos={1}
          tasks={1}
          hasModelChoice
          hasPublishedPr
        />
      </MemoryRouter>
    );
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText("Setup")).not.toBeInTheDocument();
  });

  it("skip marks a step done and persists to localStorage", async () => {
    render(
      <MemoryRouter>
        <OnboardingChecklist accounts={1} repos={0} tasks={0} />
      </MemoryRouter>
    );

    expect(screen.getByText("1 of 5 done")).toBeInTheDocument();
    const skipRepo = within(screen.getByTestId("step-repo")).getByRole("button", {
      name: "Skip Connect a repository (mark done)",
    });
    await userEvent.click(skipRepo);

    expect(screen.getByTestId("step-repo")).toHaveAttribute("data-done", "true");
    expect(screen.getByText("2 of 5 done")).toBeInTheDocument();
    expect(localStorage.getItem(ONBOARDING_MANUAL_DONE_KEY)).toContain('"repo":true');

    // A skipped step hides the card once everything else completes.
    expect(screen.queryByText("Setup")).toBeInTheDocument();
  });

  it("automatic completion hides the skip button for a previously skipped step", async () => {
    localStorage.setItem(ONBOARDING_MANUAL_DONE_KEY, JSON.stringify({ repo: true }));
    const { rerender } = render(
      <MemoryRouter>
        <OnboardingChecklist accounts={1} repos={0} tasks={0} />
      </MemoryRouter>
    );
    expect(screen.getByTestId("step-repo")).toHaveAttribute("data-done", "true");

    rerender(
      <MemoryRouter>
        <OnboardingChecklist accounts={1} repos={1} tasks={0} />
      </MemoryRouter>
    );
    expect(screen.getByTestId("step-repo")).toHaveAttribute("data-done", "true");
    expect(
      within(screen.getByTestId("step-repo")).queryByRole("button", {
        name: "Skip Connect a repository (mark done)",
      })
    ).not.toBeInTheDocument();
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

  it("survives corrupt manual-done storage", () => {
    localStorage.setItem(ONBOARDING_MANUAL_DONE_KEY, "not-json{{{");
    render(
      <MemoryRouter>
        <OnboardingChecklist accounts={0} repos={0} tasks={0} />
      </MemoryRouter>
    );
    expect(screen.getByText("Setup")).toBeInTheDocument();
    expect(screen.getByText("0 of 5 done")).toBeInTheDocument();
  });

  it("ignores the legacy v1 dismiss key so upgraders see the new steps", () => {
    localStorage.setItem("jalebi-onboarding-dismissed", "1");
    render(
      <MemoryRouter>
        <OnboardingChecklist accounts={0} repos={0} tasks={0} />
      </MemoryRouter>
    );
    expect(screen.getByText("Setup")).toBeInTheDocument();
    expect(screen.getByText("0 of 5 done")).toBeInTheDocument();
  });

  it("satisfies target size contract with min-h-6 on dismiss and skip buttons", () => {
    render(
      <MemoryRouter>
        <OnboardingChecklist accounts={0} repos={0} tasks={0} />
      </MemoryRouter>
    );
    const dismissBtn = screen.getByRole("button", { name: /dismiss/i });
    expect(dismissBtn.className).toMatch(/\bmin-h-6\b/);
    const skipBtns = screen.getAllByRole("button", { name: /Skip .* \(mark done\)/ });
    expect(skipBtns.length).toBeGreaterThan(0);
    for (const btn of skipBtns) {
      expect(btn.className).toMatch(/\bmin-h-6\b/);
    }
  });
});
