/**
 * Mission Control theme preference persistence.
 *
 * Supports two mission themes:
 * - 'ops' (Ops Deck — terminal/process grid operations console, default)
 * - 'brew' (Halwai — artisanal culinary kitchen theme)
 */

export type MissionTheme = "ops" | "brew";

const STORAGE_KEY = "jalebi-mission-theme";

/**
 * Read the user's selected mission-control theme.
 * Returns 'brew' ONLY when localStorage explicitly holds 'brew'.
 * Otherwise defaults to 'ops'.
 */
export function getMissionTheme(): MissionTheme {
  try {
    const val = localStorage.getItem(STORAGE_KEY);
    return val === "brew" ? "brew" : "ops";
  } catch {
    return "ops";
  }
}

/**
 * Persist the user's selected mission-control theme to localStorage.
 */
export function setMissionTheme(theme: MissionTheme): void {
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    /* private-mode storage — non-fatal */
  }
}
