/** Dealt-state for screening findings (per-browser localStorage).
 *
 * A finding counts as dealt once a task has been successfully created from
 * it, or when marked dealt manually. Fingerprints are semantic
 * (screen + title + file + line) — deliberately unchanged so this refactor
 * does not alter existing hide/show behavior. Moved here from
 * pages/Screenings.tsx so the Tasks page can mark handoff findings dealt
 * only after its create POST succeeds.
 */

export const FINDINGS_DEALT_KEY = "jalebi-findings-dealt";

export function findingFp(
  screenId: number,
  f: {
    title: string | null;
    file: string | null;
    line: number | null;
  }
): string {
  return JSON.stringify([screenId, f.title ?? "", f.file ?? "", f.line ?? null]);
}

export function loadDealt(): Set<string> {
  try {
    const raw = localStorage.getItem(FINDINGS_DEALT_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    if (Array.isArray(parsed)) return new Set(parsed.filter((x) => typeof x === "string"));
  } catch {
    // Corrupt or unavailable storage — start empty.
  }
  return new Set();
}

export function storeDealt(dealt: Set<string>): void {
  try {
    localStorage.setItem(FINDINGS_DEALT_KEY, JSON.stringify([...dealt]));
  } catch {
    // Private mode etc. — dealt state simply doesn't persist.
  }
}

export function markFindingsDealt(fps: string[]): void {
  if (fps.length === 0) return;
  const dealt = loadDealt();
  for (const fp of fps) dealt.add(fp);
  storeDealt(dealt);
}
