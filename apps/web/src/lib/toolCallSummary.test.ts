import { describe, expect, it } from "vitest";
import { summarizeToolCall } from "./toolCallSummary";

describe("summarizeToolCall", () => {
  it("titles tool + first input scalar, caps details", () => {
    const blob = JSON.stringify({
      session_id: "ses_1",
      tool: "bash",
      title: "git status",
      status: "completed",
      input: { command: "git status; echo hi" },
      output: "x".repeat(5000),
    });
    const { title, details } = summarizeToolCall(blob);
    expect(title).toBe("bash — git status");
    expect(details).toContain("git status; echo hi");
    expect(details).toContain("… (truncated, full output in artifacts)");
    expect(details).not.toContain("ses_1");
  });

  it("falls back to the input preview when no title", () => {
    const blob = JSON.stringify({ tool: "read", input: { filePath: "/x/y.py" } });
    expect(summarizeToolCall(blob).title).toBe("read — /x/y.py");
  });

  it("truncates unparseable (backend-capped) blobs to one line", () => {
    const raw = `{"tool": "read", "input": {"filePath": "/very/long/${"p".repeat(200)}"}, "output": "…`;
    const { title, details } = summarizeToolCall(raw);
    expect(title.length).toBeLessThanOrEqual(121);
    expect(title.endsWith("…")).toBe(true);
    expect(details).toBe(raw);
  });

  it("handles empty input", () => {
    expect(summarizeToolCall(null)).toEqual({ title: "tool call", details: "" });
    expect(summarizeToolCall("").title).toBe("tool call");
  });
});
