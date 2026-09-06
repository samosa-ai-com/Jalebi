/**
 * Attention value pill (Phase 4 T3.1).
 *
 * One-word owner-facing status surfaced by `task.attention` on every task
 * dict (T2). Rendered next to / as part of the Status cell on Tasks
 * rows, and as the centerpiece of the WaitingCard (T0) on TaskDetail.
 */
import type { AttentionValue } from "../types";

const ATTENTION_STYLE: Record<AttentionValue, { wrap: string; dot: string }> = {
  needs_you: {
    wrap: "bg-syrup-500/10 text-syrup-300 ring-syrup-500/40",
    dot: "bg-syrup-400 animate-pulse-dot",
  },
  working: {
    wrap: "bg-ink-700/30 text-ink-300 ring-ink-600/40",
    dot: "bg-syrup-400",
  },
  in_review: {
    wrap: "bg-chai-500/10 text-chai-300 ring-chai-500/40",
    dot: "bg-chai-400",
  },
  ready_to_merge: {
    wrap: "bg-syrup-500/10 text-syrup-400 ring-syrup-500/40",
    dot: "bg-syrup-400",
  },
  done: {
    wrap: "bg-ink-700/20 text-ink-500 ring-ink-700/40",
    dot: "bg-ink-500",
  },
};

function labelFor(a: AttentionValue): string {
  return a.replace(/_/g, " ");
}

export function AttentionBadge({
  attention,
  title,
}: {
  attention: AttentionValue;
  title?: string;
}) {
  const style = ATTENTION_STYLE[attention];
  return (
    <span
      title={title ?? labelFor(attention)}
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 font-mono text-[11px] font-medium ring-1 ring-inset ${style.wrap}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} />
      {labelFor(attention)}
    </span>
  );
}

export default AttentionBadge;