export interface NextStep {
  label: string;
  /** In-page anchor to scroll to (e.g. "publish-actions"). */
  targetId?: string;
  /** External URL, opened in a new tab. */
  href?: string;
}

export interface NextStepsInput {
  type: string;
  status: string;
  prs?: number[] | null;
  pr_number?: number | null;
  repo_full_name?: string | null;
  /** Whether the follow-up composer is available (a resumable session exists). */
  canFollowUp?: boolean;
}

/** Ordered "what do I do next" suggestions for a task. Empty = hide the card. */
export function getNextSteps(input: NextStepsInput): NextStep[] {
  const { type, status } = input;
  const pr = input.prs?.[0] ?? input.pr_number ?? null;
  const canFollowUp = input.canFollowUp ?? false;

  if (status === "needs_approval") {
    // Reviews never publish — point at the follow-up instead (or nothing).
    if (type === "pr_review") {
      return canFollowUp
        ? [{ label: "Send a follow-up on this task", targetId: "followup-composer" }]
        : [];
    }
    const steps: NextStep[] = [{ label: "Review, then publish", targetId: "publish-actions" }];
    if (canFollowUp) {
      steps.push({ label: "Send a follow-up on this task", targetId: "followup-composer" });
    }
    return steps;
  }

  if (
    status === "failed" ||
    status === "timed_out" ||
    status === "cancelled" ||
    status === "interrupted"
  ) {
    const steps: NextStep[] = [{ label: "Re-run this task", targetId: "publish-actions" }];
    if (canFollowUp) {
      steps.push({ label: "Send a follow-up with more context", targetId: "followup-composer" });
    }
    return steps;
  }

  if (status !== "done") {
    return [];
  }

  if (type === "pr_review") {
    if (pr == null || !input.repo_full_name) return [];
    return [
      {
        label: `View the posted review on PR #${pr}`,
        href: `https://github.com/${input.repo_full_name}/pull/${pr}`,
      },
    ];
  }

  if (pr != null) {
    // A PR exists but the repo is unknown: no URL can be built, and the
    // publish prompt would wrongly suggest opening a PR that already exists.
    // A follow-up is still a valid action when a session exists.
    if (!input.repo_full_name) {
      return canFollowUp
        ? [{ label: "Send a follow-up on this task", targetId: "followup-composer" }]
        : [];
    }
    const steps: NextStep[] = [
      {
        label: `Open pull request #${pr} on GitHub`,
        href: `https://github.com/${input.repo_full_name}/pull/${pr}`,
      },
    ];
    if (canFollowUp) {
      steps.push({ label: "Send a follow-up on this task", targetId: "followup-composer" });
    }
    return steps;
  }

  // Done with no PR: the work is waiting to be published.
  return [{ label: "Publish to open a pull request", targetId: "publish-actions" }];
}
