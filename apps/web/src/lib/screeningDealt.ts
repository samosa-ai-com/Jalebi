/** Dealt-state for screening findings — server-authoritative, cached locally.
 *
 * The backend owns dealt state (`screening_dealt` table) so audit reruns can
 * see it. Fingerprints are semantic (screen + title + file + line), computed
 * byte-identically to the backend canonical form. The legacy per-browser
 * localStorage set is only an import source (one-time migration) and an
 * instant initial cache to avoid a dealt flash before the API resolves.
 */

export const FINDINGS_DEALT_KEY = "jalebi-findings-dealt";
const DEALT_IMPORTED_KEY = "jalebi-dealt-imported";

export function findingFp(
  screenId: number,
  f: {
    title: string | null;
    file: string | null;
    line: number | null;
  }
): string {
  // Matches the backend canonical form exactly (null title → "(untitled)",
  // null file → "", non-int line → null).
  return JSON.stringify([screenId, f.title ?? "(untitled)", f.file ?? "", f.line ?? null]);
}

/** The screen id encoded in a fingerprint, or null when unparseable. */
export function screenIdOfFp(fp: string): number | null {
  try {
    const parsed: unknown = JSON.parse(fp);
    if (Array.isArray(parsed) && typeof parsed[0] === "number") return parsed[0];
  } catch {
    // Not a fingerprint — ignored.
  }
  return null;
}

/** Legacy browser-local set: initial cache + one-time import source. */
export function readLegacyDealt(): Set<string> {
  try {
    const raw = localStorage.getItem(FINDINGS_DEALT_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    if (Array.isArray(parsed)) return new Set(parsed.filter((x) => typeof x === "string"));
  } catch {
    // Corrupt or unavailable storage — start empty.
  }
  return new Set();
}

export function clearLegacyDealt(): void {
  try {
    localStorage.removeItem(FINDINGS_DEALT_KEY);
  } catch {
    // Private mode etc. — nothing to clear.
  }
}

export function isDealtImported(): boolean {
  try {
    return localStorage.getItem(DEALT_IMPORTED_KEY) === "1";
  } catch {
    return false;
  }
}

export function setDealtImported(): void {
  try {
    localStorage.setItem(DEALT_IMPORTED_KEY, "1");
  } catch {
    // Private mode etc. — import simply retries next mount.
  }
}

/** Group fingerprints by their encoded screen id (unknown screens dropped). */
export function groupFpsByScreen(fps: Iterable<string>): Map<number, string[]> {
  const byScreen = new Map<number, string[]>();
  for (const fp of fps) {
    const sid = screenIdOfFp(fp);
    if (sid === null) continue;
    const list = byScreen.get(sid);
    if (list) list.push(fp);
    else byScreen.set(sid, [fp]);
  }
  return byScreen;
}
