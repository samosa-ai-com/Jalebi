import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { api } from "../api/client";
import { useFocusTrap } from "../lib/useFocusTrap";

export type PublishMode = "new_pr" | "update_pr" | "push_branch";

export interface PublishOptions {
  mode: PublishMode;
  branch?: string;
  pr_number?: number;
}

interface PublishDialogProps {
  taskId: number;
  options: PublishOptions;
  onClose: () => void;
  onPublished: () => void;
}

const MODE_LABEL: Record<PublishMode, string> = {
  new_pr: "Open a new PR",
  update_pr: "Update existing PR",
  push_branch: "Push to branch",
};

function targetDescription(options: PublishOptions): string {
  if (options.mode === "new_pr") return "jalebi/<id> → task target branch (opens a new PR)";
  if (options.mode === "update_pr") return `update PR #${options.pr_number} head branch`;
  return `push onto \`${options.branch}\``;
}

/**
 * Confirmation dialog for a publish action. Surfaces a yellow force-push
 * warning for `update_pr` / `push_branch` modes (the remote branch will be
 * overwritten), shows the target, and posts the publish request on confirm.
 *
 * Rendered via `createPortal` into `document.body`: TaskDetail's root carries
 * `animate-fade-up` (a persisting `transform`), which would otherwise capture
 * `position: fixed` and center the dialog in the whole tall page instead of
 * the viewport.
 */
export default function PublishDialog({
  taskId,
  options,
  onClose,
  onPublished,
}: PublishDialogProps) {
  const dialogRef = useFocusTrap<HTMLDivElement>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Hold the latest onClose in a ref so the Escape-key effect registers once
  // for the dialog's lifetime instead of on every render (the parent passes
  // an inline arrow that changes identity each render).
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !busy) onCloseRef.current();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [busy]);

  const isForcePush = options.mode === "update_pr" || options.mode === "push_branch";

  function closeIfIdle() {
    if (!busy) onClose();
  }

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await api.publishTask(taskId, options);
      onPublished();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "publish failed");
    } finally {
      setBusy(false);
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <button
        type="button"
        className="fixed inset-0 cursor-default border-0 bg-transparent"
        tabIndex={-1}
        aria-label="Close dialog"
        onClick={closeIfIdle}
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        tabIndex={-1}
        className="surface relative z-10 w-full max-w-md space-y-4 p-5 animate-fade-up"
      >
        <div>
          <h2 className="text-sm font-semibold text-ink-100">{MODE_LABEL[options.mode]}</h2>
          <p className="mt-1 text-xs text-ink-400">
            Target: <span className="font-mono text-ink-200">{targetDescription(options)}</span>
          </p>
        </div>

        {isForcePush && (
          <div className="rounded-md border border-amber-700/60 bg-amber-900/20 p-3 text-xs text-amber-200">
            <strong>Force-update:</strong> the remote branch will be overwritten with the
            agent&apos;s commits. The push uses <code>--force-with-lease</code> so concurrent pushes
            by others refuse instead of clobbering their work.
          </div>
        )}

        {options.mode === "new_pr" && (
          <p className="text-xs text-ink-400">
            A new pull request will be opened into the task&apos;s target branch. Linked issues (for{" "}
            <code>issue_fix</code>) will be commented with the PR link.
          </p>
        )}

        {error && <p className="text-xs text-red-400">{error}</p>}

        <div className="flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={closeIfIdle} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary disabled:opacity-40"
            onClick={confirm}
            disabled={busy}
          >
            {busy ? "Publishing…" : "Confirm"}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
