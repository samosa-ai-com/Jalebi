import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { useFocusTrap } from "../lib/useFocusTrap";

interface BackendMissingDialogProps {
  missing: string;
  installed: string[];
  onPick: (cli: string) => void;
  onClose: () => void;
}

/**
 * Blocks task creation when the selected backend isn't installed: no task
 * is created — the owner picks an installed backend (applied to the form)
 * or opens Settings instead.
 *
 * Rendered via `createPortal` into `document.body` (same reason as
 * PublishDialog: persisting `transform` animations would capture
 * `position: fixed`).
 */
export default function BackendMissingDialog({
  missing,
  installed,
  onPick,
  onClose,
}: BackendMissingDialogProps) {
  const dialogRef = useFocusTrap<HTMLDivElement>();
  const [busy, setBusy] = useState(false);
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

  function closeIfIdle() {
    if (!busy) onClose();
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
        aria-label="Backend not installed"
        tabIndex={-1}
        className="surface relative z-10 w-full max-w-md space-y-4 p-5 animate-fade-up"
      >
        <div>
          <h2 className="text-sm font-semibold text-ink-100">Backend not installed</h2>
          <p className="mt-1 text-xs text-ink-400">
            <span className="font-mono text-ink-200">{missing}</span> isn&apos;t installed on
            this machine, so no task was created. Pick one that is:
          </p>
        </div>

        {installed.length === 0 ? (
          <p className="text-xs text-ink-400">
            No backends are installed. Install one, then come back — or review the options in{" "}
            <Link to="/settings?section=agent" className="link" onClick={closeIfIdle}>
              Settings
            </Link>
            .
          </p>
        ) : (
          <ul className="space-y-1.5">
            {installed.map((cli) => (
              <li key={cli}>
                <button
                  type="button"
                  className="btn-ghost w-full !justify-start font-mono text-xs"
                  onClick={() => {
                    setBusy(true);
                    try {
                      onPick(cli);
                    } finally {
                      setBusy(false);
                    }
                    onClose();
                  }}
                >
                  Use {cli}
                </button>
              </li>
            ))}
          </ul>
        )}

        <div className="flex justify-end gap-2">
          <Link to="/settings?section=agent" className="btn-ghost text-xs" onClick={closeIfIdle}>
            Open Settings
          </Link>
          <button type="button" className="btn-ghost" onClick={closeIfIdle} disabled={busy}>
            Cancel
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
