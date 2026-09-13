/** One-line plain-language definitions for UI jargon (tooltip text).
 * Mirrored in docs/08-ui.md — keep both in sync when adding terms. */
export const GLOSSARY: Record<string, string> = {
  backend:
    "The AI coding assistant (opencode, codex, or claude) that runs on your machine to do the task.",
  "dismiss-attention": "Clears the notification badge only — the task and its code are untouched.",
  artifacts: "Files the agent produced but never committed to git (test output, images, downloads).",
  phases: "The stages an agent moves through, from first look at the problem to finished work.",
  "timeline-step-types": "Kinds of run events: agent messages, tool actions, and file diffs.",
  "base-ref": "The upstream branch these changes are compared against.",
  dealt: "Marked as handled — reviewed, dismissed, or turned into a task.",
  severity: "How risky the finding is, as judged by the audit: critical, high, medium, or low.",
  "screen-template": "A ready-made audit setup (prompt plus schedule) for a common check.",
  synchronize: "GitHub event fired when new commits are pushed to an open pull request.",
};
