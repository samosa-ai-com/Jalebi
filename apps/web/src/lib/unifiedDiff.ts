/**
 * Pure TS unified-diff parser (Phase 4 T3.3).
 *
 * Splits a `git diff` body into per-file blocks with status, add/del counts,
 * and individual hunks. Used by the upgraded TaskDetail diff viewer.
 */

export type DiffStatus =
  | "added"
  | "removed"
  | "modified"
  | "renamed"
  | "binary";

export interface DiffHunk {
  /** The raw `@@ -l,s +l,s @@ optional heading` header line. */
  header: string;
  /** Lines starting with `+`, `-`, ` ` (context), or the literal `\ No newline…`. */
  lines: string[];
}

export interface DiffFile {
  oldPath: string | null;
  newPath: string | null;
  status: DiffStatus;
  additions: number;
  deletions: number;
  binary: boolean;
  /** Raw "rename from" / "rename to" if git emitted them (renames only). */
  renameFrom?: string;
  renameTo?: string;
  hunks: DiffHunk[];
}

const FILE_HEADER = /^diff --git a\/(.*?) b\/(.*)$/;
const HUNK_HEADER = /^@@ /;
const RENAME_FROM = /^rename from (.+)$/;
const RENAME_TO = /^rename to (.+)$/;
const NEW_FILE_MODE = /^new file mode /;
const BINARY_FILES = /^Binary files (.+?) and (.+?) differ$/;

export function parseUnifiedDiff(text: string): DiffFile[] {
  if (!text) return [];
  const lines = text.split("\n");
  const out: DiffFile[] = [];
  let current: DiffFile | null = null;

  function finalizeHunk(hunkLines: string[]) {
    if (!current) return;
    if (hunkLines.length === 0) return;
    let header = "";
    let i = 0;
    if (hunkLines[0] && HUNK_HEADER.test(hunkLines[0])) {
      header = hunkLines[0];
      i = 1;
    }
    const body = hunkLines.slice(i);
    current.hunks.push({ header, lines: body });
    for (const line of body) {
      if (line === "\\ No newline at end of file") continue;
      if (line.startsWith("+++ ") || line.startsWith("--- ")) continue;
      if (line.startsWith("+")) current.additions += 1;
      else if (line.startsWith("-")) current.deletions += 1;
    }
  }

  let pendingHunk: string[] | null = null;

  for (const line of lines) {
    const headerMatch = line.match(FILE_HEADER);
    if (headerMatch) {
      if (current) {
        if (pendingHunk) {
          finalizeHunk(pendingHunk);
          pendingHunk = null;
        }
        out.push(current);
      }
      const [, oldPath, newPath] = headerMatch;
      current = {
        oldPath,
        newPath,
        status: "modified",
        additions: 0,
        deletions: 0,
        binary: false,
        hunks: [],
      };
      continue;
    }
    if (!current) continue;

    if (NEW_FILE_MODE.test(line)) {
      current.status = "added";
      current.oldPath = null;
      continue;
    }
    if (line.startsWith("deleted file mode")) {
      current.status = "removed";
      current.newPath = null;
      continue;
    }
    const renameFromMatch = line.match(RENAME_FROM);
    if (renameFromMatch) {
      current.status = "renamed";
      current.oldPath = renameFromMatch[1];
      current.renameFrom = renameFromMatch[1];
      continue;
    }
    const renameToMatch = line.match(RENAME_TO);
    if (renameToMatch) {
      current.status = "renamed";
      current.newPath = renameToMatch[1];
      current.renameTo = renameToMatch[1];
      continue;
    }
    if (BINARY_FILES.test(line)) {
      current.binary = true;
      current.status = "binary";
      current.hunks = [];
      continue;
    }
    if (HUNK_HEADER.test(line)) {
      if (pendingHunk) finalizeHunk(pendingHunk);
      pendingHunk = [line];
      continue;
    }
    if (pendingHunk) {
      pendingHunk.push(line);
      continue;
    }
    // Outside a hunk: header meta-lines we already handled above; otherwise
    // we ignore (covers stray "index abc..def 100644" / "similarity index 100%"
    // / old-format diff headers / trailing blank lines).
  }
  if (current) {
    if (pendingHunk) finalizeHunk(pendingHunk);
    out.push(current);
  }
  return out;
}