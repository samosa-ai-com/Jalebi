import { useEffect, useRef, useState } from "react";

export interface TaskStatusSnapshot {
  id: number;
  status: string;
  attention?: string | null;
}

const TERMINAL_STATUS_WORDS: Record<string, string> = {
  done: "done",
  failed: "failed",
  timed_out: "timed out",
  interrupted: "interrupted",
};

/**
 * Tracks previous task states and produces polite announcement strings for screen readers
 * when tasks transition into terminal states or require user input.
 *
 * Announce only transitions, never the steady state.
 * Caps at 3 messages joined with '; ' per update.
 */
export function useStatusAnnouncer(tasks: TaskStatusSnapshot[] = []): string {
  const [announcement, setAnnouncement] = useState("");
  const prevMapRef = useRef<Map<number, { status: string; attention?: string | null }>>(new Map());
  const isFirstMountRef = useRef(true);

  useEffect(() => {
    if (isFirstMountRef.current) {
      isFirstMountRef.current = false;
      const nextMap = new Map<number, { status: string; attention?: string | null }>();
      for (const t of tasks) {
        nextMap.set(t.id, { status: t.status, attention: t.attention });
      }
      prevMapRef.current = nextMap;
      return;
    }

    const messages: string[] = [];

    for (const t of tasks) {
      const prev = prevMapRef.current.get(t.id);
      if (!prev) {
        // Newly appeared task without prior observation; steady state.
        continue;
      }

      const attentionBecameNeedsYou =
        prev.attention !== "needs_you" && t.attention === "needs_you";

      const statusWord = TERMINAL_STATUS_WORDS[t.status];
      const statusNewlyTransitioned =
        statusWord !== undefined && prev.status !== t.status;

      if (attentionBecameNeedsYou) {
        messages.push(`Task ${t.id} needs your input`);
      } else if (statusNewlyTransitioned) {
        messages.push(`Task ${t.id} ${statusWord}`);
      }
    }

    const nextMap = new Map<number, { status: string; attention?: string | null }>();
    for (const t of tasks) {
      nextMap.set(t.id, { status: t.status, attention: t.attention });
    }
    prevMapRef.current = nextMap;

    setAnnouncement(messages.length > 0 ? messages.slice(0, 3).join("; ") : "");
  }, [tasks]);

  return announcement;
}
