import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { TaskNotification } from "../types";

function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const zoned = /([zZ]|[+-]\d{2}:?\d{2})$/.test(iso);
  const parsed = new Date(zoned ? iso : `${iso}Z`);
  if (Number.isNaN(parsed.getTime())) return "—";
  const s = Math.max(0, (Date.now() - parsed.getTime()) / 1000);
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function KindIcon({ kind }: { kind: string }) {
  if (kind === "task_done") {
    return (
      <svg
        className="h-4 w-4 shrink-0 text-green-400 mt-0.5"
        viewBox="0 0 20 20"
        fill="currentColor"
        aria-hidden="true"
        data-testid="kind-icon"
        data-kind={kind}
      >
        <path
          fillRule="evenodd"
          d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
          clipRule="evenodd"
        />
      </svg>
    );
  }
  if (kind === "task_failed") {
    return (
      <svg
        className="h-4 w-4 shrink-0 text-red-400 mt-0.5"
        viewBox="0 0 20 20"
        fill="currentColor"
        aria-hidden="true"
        data-testid="kind-icon"
        data-kind={kind}
      >
        <path
          fillRule="evenodd"
          d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
          clipRule="evenodd"
        />
      </svg>
    );
  }
  if (kind === "needs_input") {
    return (
      <span
        className="h-2.5 w-2.5 shrink-0 rounded-full bg-amber-400 mt-1.5"
        data-testid="kind-icon"
        data-kind={kind}
        aria-hidden="true"
      />
    );
  }
  return (
    <span
      className="h-2.5 w-2.5 shrink-0 rounded-full bg-ink-400 mt-1.5"
      data-testid="kind-icon"
      data-kind={kind}
      aria-hidden="true"
    />
  );
}

export default function NotificationBell() {
  const [open, setOpen] = useState(false);
  const [unread, setUnread] = useState(0);
  const [notifications, setNotifications] = useState<TaskNotification[]>([]);
  const rootRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  // Close on outside click and on Escape
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  // The unread badge and the Mark-all-read action always follow the server
  // count — never the visible page. The list endpoint returns only the newest
  // page, so deriving the count from it would hide older unread rows.
  const refreshCount = useCallback(async () => {
    try {
      const res = await api.getUnreadCount();
      if (res && typeof res.unread === "number") {
        setUnread(res.unread);
      }
    } catch {
      // best effort, keep previous state
    }
  }, []);

  useEffect(() => {
    // Polling fetch: the state update happens asynchronously after the fetch
    // resolves, not during render — this is not a cascading render.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refreshCount();
    const timer = setInterval(refreshCount, 10000);
    return () => clearInterval(timer);
  }, [refreshCount]);

  // Latest page of notifications while the panel is open.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const fetchList = async () => {
      try {
        const list = await api.getNotifications();
        if (!cancelled && Array.isArray(list)) {
          setNotifications(list);
        }
      } catch {
        // best effort, keep previous state
      }
    };
    fetchList();
    const timer = setInterval(fetchList, 10000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [open ]);

  const hasUnread = unread > 0;

  const handleMarkAllRead = async () => {
    try {
      await api.markAllNotificationsRead();
      setNotifications((prev) =>
        prev.map((n) => ({ ...n, read_at: n.read_at || new Date().toISOString() }))
      );
      await refreshCount();
    } catch {
      // best effort, keep previous state
    }
  };

  const handleRowClick = async (n: TaskNotification) => {
    try {
      await api.markNotificationRead(n.id);
      setNotifications((prev) =>
        prev.map((item) =>
          item.id === n.id ? { ...item, read_at: item.read_at || new Date().toISOString() } : item
        )
      );
      await refreshCount();
    } catch {
      // best effort, keep previous state
    } finally {
      setOpen(false);
      navigate(`/tasks/${n.task_id}`);
    }
  };

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-label="Notifications"
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
        className="relative flex items-center justify-center rounded-lg p-2 text-ink-400 hover:text-ink-100 hover:bg-ink-850 transition-colors"
      >
        <svg
          className="h-5 w-5"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.73 21a2 2 0 0 1-3.46 0" />
        </svg>
        {unread > 0 && (
          <span
            data-testid="unread-badge"
            className="absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-syrup-500 px-1 text-[10px] font-bold text-ink-950"
          >
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div
          data-testid="notification-panel"
          className="absolute right-0 mt-2 w-80 sm:w-96 max-h-96 rounded-xl border border-ink-800 bg-ink-900 shadow-2xl z-50 flex flex-col overflow-hidden"
        >
          <div className="flex items-center justify-between border-b border-ink-800 px-4 py-2.5 shrink-0">
            <span className="font-semibold text-sm text-ink-100">Notifications</span>
            {hasUnread && (
              <button
                type="button"
                onClick={handleMarkAllRead}
                className="text-xs font-medium text-syrup-400 hover:text-syrup-300 transition-colors"
              >
                Mark all read
              </button>
            )}
          </div>
          <div className="overflow-y-auto flex-1 divide-y divide-ink-800/60">
            {notifications.length === 0 ? (
              <div className="p-6 text-center text-sm text-ink-400">
                You are all caught up.
              </div>
            ) : (
              notifications.map((n) => (
                <button
                  key={n.id}
                  type="button"
                  onClick={() => handleRowClick(n)}
                  className="w-full text-left px-4 py-3 hover:bg-ink-850 transition-colors flex items-start gap-3 cursor-pointer"
                >
                  <KindIcon kind={n.kind} />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-ink-100 truncate">{n.title}</div>
                    <div className="text-xs text-ink-400 mt-0.5">
                      #{n.task_id} · {timeAgo(n.created_at)}
                    </div>
                  </div>
                  {!n.read_at && (
                    <span
                      data-testid="unread-dot"
                      aria-label="Unread"
                      title="Unread"
                      className="h-2 w-2 shrink-0 rounded-full bg-syrup-500 mt-1.5"
                    />
                  )}
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
