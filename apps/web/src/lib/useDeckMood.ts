import { useEffect, useMemo, useRef, useState } from "react";
import type { Task } from "../types";

/**
 * Shared mission-control mood engine.
 *
 * Derives a deck-wide mood from task state and a transient one-shot flash,
 * consumed by BOTH themes (Ops Deck and Halwai) so the status-reactive
 * energy borders / circuit pulse behave identically. Theme-agnostic: the
 * component only sets `data-mood` / `data-flash` on a `.mission-deck` root.
 */

export type DeckMood = "idle" | "active" | "attention" | "fault";
export type DeckFlash = "none" | "celebrate" | "fault";

function parseTime(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const zoned = /([zZ]|[+-]\d{2}:?\d{2})$/.test(iso);
  const t = new Date(zoned ? iso : `${iso}Z`).getTime();
  return Number.isNaN(t) ? null : t;
}

const FIVE_MINUTES_MS = 5 * 60 * 1000;
const FLASH_DURATION_MS = 2500;

function deriveMood(tasks: Task[], now: number): DeckMood {
  const hasRecentFault = tasks.some((t) => {
    if (
      t.status !== "failed" &&
      t.status !== "timed_out" &&
      t.status !== "interrupted"
    ) {
      return false;
    }
    const updatedMs = parseTime(t.updated_at);
    if (updatedMs === null) return false;
    const diff = now - updatedMs;
    return diff >= -60_000 && diff <= FIVE_MINUTES_MS;
  });

  if (hasRecentFault) return "fault";

  const hasAttention = tasks.some((t) => t.attention === "needs_you");
  if (hasAttention) return "attention";

  const hasRunning = tasks.some((t) => t.status === "running");
  if (hasRunning) return "active";

  return "idle";
}

export function useDeckMood(
  tasks: Task[],
  nowOverride?: number
): { mood: DeckMood; flash: DeckFlash } {
  const [now, setNow] = useState(() => nowOverride ?? Date.now());
  const [flash, setFlash] = useState<DeckFlash>("none");
  const prevDoneIdsRef = useRef<Set<number> | null>(null);
  const prevFaultIdsRef = useRef<Set<number> | null>(null);
  // True while we have only ever seen the initial empty snapshot (the Tasks
  // page renders before its first fetch resolves). The first real snapshot is
  // the baseline, so the first load never fires a false celebrate flash.
  const emptyLoadRef = useRef(false);
  const flashTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const mood = useMemo<DeckMood>(() => {
    const effectiveNow = nowOverride ?? now;
    return deriveMood(tasks, effectiveNow);
  }, [tasks, nowOverride, now]);

  // The 5-minute fault window must expire even if the task array never
  // changes (poll paused, tab in background), so advance `now` periodically.
  useEffect(() => {
    if (nowOverride !== undefined) return;
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, [nowOverride]);

  useEffect(() => {
    const currentDoneIds = new Set<number>();
    const currentFaultIds = new Set<number>();

    for (const t of tasks) {
      if (t.status === "done") {
        currentDoneIds.add(t.id);
      } else if (
        t.status === "failed" ||
        t.status === "timed_out" ||
        t.status === "interrupted"
      ) {
        currentFaultIds.add(t.id);
      }
    }

    // On initial mount, record existing sets without triggering flash
    if (prevDoneIdsRef.current === null || prevFaultIdsRef.current === null) {
      prevDoneIdsRef.current = currentDoneIds;
      prevFaultIdsRef.current = currentFaultIds;
      emptyLoadRef.current = tasks.length === 0;
      return;
    }

    // Still on the initial (empty) load — wait for the first real snapshot and
    // treat it as the baseline rather than a transition.
    if (emptyLoadRef.current) {
      if (tasks.length === 0) return;
      emptyLoadRef.current = false;
      prevDoneIdsRef.current = currentDoneIds;
      prevFaultIdsRef.current = currentFaultIds;
      return;
    }

    let gainedDone = false;
    for (const id of currentDoneIds) {
      if (!prevDoneIdsRef.current.has(id)) {
        gainedDone = true;
        break;
      }
    }

    let gainedFault = false;
    for (const id of currentFaultIds) {
      if (!prevFaultIdsRef.current.has(id)) {
        gainedFault = true;
        break;
      }
    }

    prevDoneIdsRef.current = currentDoneIds;
    prevFaultIdsRef.current = currentFaultIds;

    if (gainedFault || gainedDone) {
      if (flashTimerRef.current !== null) {
        clearTimeout(flashTimerRef.current);
        flashTimerRef.current = null;
      }

      // Most recent transition wins (fault takes precedence if both transition simultaneously)
      const nextFlash: DeckFlash = gainedFault ? "fault" : "celebrate";
      setFlash(nextFlash);

      flashTimerRef.current = setTimeout(() => {
        setFlash("none");
        flashTimerRef.current = null;
      }, FLASH_DURATION_MS);
    }
  }, [tasks]);

  useEffect(() => {
    return () => {
      if (flashTimerRef.current !== null) {
        clearTimeout(flashTimerRef.current);
        flashTimerRef.current = null;
      }
    };
  }, []);

  return { mood, flash };
}
