/** Phase 4 T3.3 — pure TS unified-diff parser unit tests. */

import { describe, expect, it } from "vitest";

import { parseUnifiedDiff } from "./unifiedDiff";

describe("parseUnifiedDiff", () => {
  it("returns [] on empty input", () => {
    expect(parseUnifiedDiff("")).toEqual([]);
  });

  it("returns [] on malformed input (no diff --git header)", () => {
    expect(parseUnifiedDiff("just a stray line\nanother stray line\n")).toEqual([]);
  });

  it("parses an added file", () => {
    const diff = [
      "diff --git a/new.txt b/new.txt",
      "new file mode 100644",
      "index 0000000..abc123",
      "--- /dev/null",
      "+++ b/new.txt",
      "@@ -0,0 +1,2 @@",
      "+hello",
      "+world",
    ].join("\n");
    const [file] = parseUnifiedDiff(diff);
    expect(file.status).toBe("added");
    expect(file.oldPath).toBeNull();
    expect(file.newPath).toBe("new.txt");
    expect(file.additions).toBe(2);
    expect(file.deletions).toBe(0);
    expect(file.hunks).toHaveLength(1);
    expect(file.hunks[0].header).toBe("@@ -0,0 +1,2 @@");
    expect(file.hunks[0].lines).toEqual(["+hello", "+world"]);
  });

  it("parses a removed file", () => {
    const diff = [
      "diff --git a/gone.txt b/gone.txt",
      "deleted file mode 100644",
      "index abc123..0000000",
      "--- a/gone.txt",
      "+++ /dev/null",
      "@@ -1 +0,0 @@",
      "-bye",
    ].join("\n");
    const [file] = parseUnifiedDiff(diff);
    expect(file.status).toBe("removed");
    expect(file.oldPath).toBe("gone.txt");
    expect(file.newPath).toBeNull();
    expect(file.additions).toBe(0);
    expect(file.deletions).toBe(1);
  });

  it("parses a modified file with multiple hunks", () => {
    const diff = [
      "diff --git a/file.py b/file.py",
      "index abc..def 100644",
      "--- a/file.py",
      "+++ b/file.py",
      "@@ -1,3 +1,3 @@",
      "-old1",
      "+new1",
      " context",
      "@@ -10,2 +10,2 @@",
      " ctx",
      "-old2",
      "+new2",
    ].join("\n");
    const [file] = parseUnifiedDiff(diff);
    expect(file.status).toBe("modified");
    expect(file.additions).toBe(2);
    expect(file.deletions).toBe(2);
    expect(file.hunks).toHaveLength(2);
    expect(file.hunks[0].header).toBe("@@ -1,3 +1,3 @@");
    expect(file.hunks[1].header).toBe("@@ -10,2 +10,2 @@");
  });

  it("parses a renamed file (carries both paths)", () => {
    const diff = [
      "diff --git a/old/name.ts b/new/name.ts",
      "similarity index 95%",
      "rename from old/name.ts",
      "rename to new/name.ts",
      "index abc..def 100644",
      "--- a/old/name.ts",
      "+++ b/new/name.ts",
      "@@ -1 +1 @@",
      "-same",
      "+same",
    ].join("\n");
    const [file] = parseUnifiedDiff(diff);
    expect(file.status).toBe("renamed");
    expect(file.oldPath).toBe("old/name.ts");
    expect(file.newPath).toBe("new/name.ts");
    expect(file.renameFrom).toBe("old/name.ts");
    expect(file.renameTo).toBe("new/name.ts");
  });

  it("parses a binary file (no hunks)", () => {
    const diff =
      "diff --git a/img.png b/img.png\n" +
      "index abc..def 100644\n" +
      "Binary files a/img.png and b/img.png differ\n";
    const [file] = parseUnifiedDiff(diff);
    expect(file.status).toBe("binary");
    expect(file.binary).toBe(true);
    expect(file.hunks).toEqual([]);
    expect(file.additions).toBe(0);
    expect(file.deletions).toBe(0);
  });

  it("ignores \\ No newline at end of file markers", () => {
    const diff = [
      "diff --git a/f.txt b/f.txt",
      "--- a/f.txt",
      "+++ b/f.txt",
      "@@ -1 +1 @@",
      "-old",
      "\\ No newline at end of file",
      "+new",
    ].join("\n");
    const [file] = parseUnifiedDiff(diff);
    expect(file.additions).toBe(1);
    expect(file.deletions).toBe(1);
  });

  it("handles multiple files in one diff", () => {
    const diff = [
      "diff --git a/a.txt b/a.txt",
      "--- a/a.txt",
      "+++ b/a.txt",
      "@@ -1 +1 @@",
      "-a",
      "+A",
      "diff --git a/b.txt b/b.txt",
      "--- a/b.txt",
      "+++ b/b.txt",
      "@@ -1 +1 @@",
      "-b",
      "+B",
    ].join("\n");
    const files = parseUnifiedDiff(diff);
    expect(files).toHaveLength(2);
    expect(files.map((f) => f.newPath)).toEqual(["a.txt", "b.txt"]);
    expect(files.map((f) => f.additions)).toEqual([1, 1]);
    expect(files.map((f) => f.deletions)).toEqual([1, 1]);
  });
});