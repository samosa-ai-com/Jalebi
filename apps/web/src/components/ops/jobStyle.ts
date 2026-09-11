import React from "react";

/**
 * Ops Deck — coding job semantics.
 *
 * Replaces culinary snacks with engineering semantics:
 * - freeform    → 'feature' (sky accent, angle-brackets glyph)
 * - issue_fix   → 'fix'     (amber accent, wrench/bug glyph)
 * - pr_review   → 'review'  (violet accent, review/eye glyph)
 */

export type JobKind = "feature" | "fix" | "review";

export function jobForType(type: string): JobKind {
  if (type === "issue_fix") return "fix";
  if (type === "pr_review") return "review";
  return "feature";
}

export const JOB_LABEL: Record<JobKind, string> = {
  feature: "feature",
  fix: "fix",
  review: "review",
};

export interface JobAccentClasses {
  text: string;
  bg: string;
  bgSubtle: string;
  border: string;
  borderSubtle: string;
  ring: string;
  badge: string;
  dot: string;
}

export const JOB_ACCENT: Record<JobKind, JobAccentClasses> = {
  feature: {
    text: "text-sky-400",
    bg: "bg-sky-500",
    bgSubtle: "bg-sky-500/10",
    border: "border-sky-500/40",
    borderSubtle: "border-sky-500/20",
    ring: "ring-sky-500/50",
    badge: "bg-sky-500/15 text-sky-300 border-sky-500/30",
    dot: "bg-sky-400",
  },
  fix: {
    text: "text-amber-400",
    bg: "bg-amber-500",
    bgSubtle: "bg-amber-500/10",
    border: "border-amber-500/40",
    borderSubtle: "border-amber-500/20",
    ring: "ring-amber-500/50",
    badge: "bg-amber-500/15 text-amber-300 border-amber-500/30",
    dot: "bg-amber-400",
  },
  review: {
    text: "text-violet-400",
    bg: "bg-violet-500",
    bgSubtle: "bg-violet-500/10",
    border: "border-violet-500/40",
    borderSubtle: "border-violet-500/20",
    ring: "ring-violet-500/50",
    badge: "bg-violet-500/15 text-violet-300 border-violet-500/30",
    dot: "bg-violet-400",
  },
};

export const STATUS_LABEL: Record<string, string> = {
  queued: "queued",
  running: "running",
  done: "shipped",
  failed: "failed",
  timed_out: "timed out",
  interrupted: "interrupted",
  cancelled: "cancelled",
  needs_approval: "needs approval",
  blocked: "blocked",
};

/**
 * Small accessible aria-hidden SVG glyph per kind:
 * - feature: angle brackets </>
 * - fix: wrench / bug
 * - review: eye / code review inspect
 */
export function JobGlyph({
  kind,
  className = "h-3.5 w-3.5",
}: {
  kind: JobKind;
  className?: string;
}): React.ReactElement {
  if (kind === "fix") {
    // Wrench / tool glyph
    return React.createElement(
      "svg",
      {
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: "2",
        strokeLinecap: "round",
        strokeLinejoin: "round",
        "aria-hidden": "true",
        className: `shrink-0 ${className}`,
      },
      React.createElement("path", {
        d: "M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z",
      })
    );
  }

  if (kind === "review") {
    // Review / eye inspection glyph
    return React.createElement(
      "svg",
      {
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: "2",
        strokeLinecap: "round",
        strokeLinejoin: "round",
        "aria-hidden": "true",
        className: `shrink-0 ${className}`,
      },
      React.createElement("path", {
        d: "M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z",
      }),
      React.createElement("circle", { cx: "12", cy: "12", r: "3" })
    );
  }

  // Feature: angle brackets </>
  return React.createElement(
    "svg",
    {
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      strokeWidth: "2",
      strokeLinecap: "round",
      strokeLinejoin: "round",
      "aria-hidden": "true",
      className: `shrink-0 ${className}`,
    },
    React.createElement("polyline", { points: "16 18 22 12 16 6" }),
    React.createElement("polyline", { points: "8 6 2 12 8 18" }),
    React.createElement("line", { x1: "14", y1: "4", x2: "10", y2: "20" })
  );
}
