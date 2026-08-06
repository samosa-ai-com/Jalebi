const STATUS_STYLES: Record<string, string> = {
  queued: "bg-ink-700/30 text-ink-300 ring-ink-600/40",
  running: "bg-syrup-500/10 text-syrup-300 ring-syrup-500/40",
  waiting_review: "bg-chai-500/10 text-chai-300 ring-chai-500/40",
  done: "bg-green-500/10 text-green-300 ring-green-500/40",
  failed: "bg-red-500/10 text-red-300 ring-red-500/40",
  timed_out: "bg-red-500/10 text-red-300 ring-red-500/40",
  cancelled: "bg-ink-600/20 text-ink-400 ring-ink-600/40",
  interrupted: "bg-ink-600/20 text-ink-300 ring-ink-600/40",
  needs_approval: "bg-purple-500/10 text-purple-300 ring-purple-500/40",
};

function dot(status: string): string {
  switch (status) {
    case "running":
      return "bg-syrup-400 animate-pulse-dot";
    case "queued":
      return "bg-ink-400";
    case "done":
      return "bg-green-400";
    case "failed":
    case "timed_out":
      return "bg-red-400";
    case "needs_approval":
    case "waiting_review":
      return "bg-purple-400";
    default:
      return "bg-ink-500";
  }
}

export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 font-mono text-[11px] font-medium ring-1 ring-inset ${
        STATUS_STYLES[status] ?? "bg-ink-700/30 text-ink-300 ring-ink-600/40"
      }`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${dot(status)}`} />
      {status}
    </span>
  );
}
