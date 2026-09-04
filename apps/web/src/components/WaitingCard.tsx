import { Link } from "react-router-dom";
import type { Run } from "../types";
import Markdown from "./Markdown";
import { StatusBadge } from "./StatusBadge";

export default function WaitingCard({
  run,
  message,
  canReply,
  onReply,
  ideConfigured,
  onOpenWorktree,
  onReject,
}: {
  run: Run;
  message: string;
  canReply: boolean;
  onReply: () => void;
  ideConfigured: boolean;
  onOpenWorktree: () => void;
  onReject?: () => void;
}) {
  const finished = run.finished_at ? new Date(run.finished_at).toLocaleString() : "—";
  return (
    <section className="rounded-xl border border-syrup-500/40 bg-syrup-500/[0.07] p-5">
      <div className="flex flex-wrap items-center gap-3">
        <span className="h-2.5 w-2.5 shrink-0 rounded-full bg-syrup-400 animate-pulse-dot" />
        <h2 className="font-semibold text-ink-100">Agent is waiting for your input</h2>
        <StatusBadge status={run.status ?? ""} />
        <span className="ml-auto font-mono text-[11px] text-ink-500">{finished}</span>
      </div>
      <div className="mt-3 text-sm leading-snug text-ink-300">
        <Markdown>{message}</Markdown>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button type="button" onClick={onReply} disabled={!canReply} className="btn-primary">
          Reply in follow-up
        </button>
        <button
          type="button"
          onClick={onOpenWorktree}
          disabled={!ideConfigured}
          className="btn-ghost disabled:opacity-40"
          title={
            ideConfigured
              ? "Open this task's worktree in your configured IDE"
              : "IDE not configured"
          }
        >
          Open worktree
        </button>
        {onReject && (
          <button
            type="button"
            onClick={onReject}
            className="btn-ghost text-red-400 hover:text-red-300 hover:border-red-500/40"
            title="Reject this proposal and dismiss attention"
          >
            Reject proposal
          </button>
        )}
        {!ideConfigured && (
          <Link
            to="/settings"
            className="text-[11px] text-ink-500 underline-offset-2 hover:text-ink-300 hover:underline"
          >
            configure IDE in Settings
          </Link>
        )}
      </div>
    </section>
  );
}