/** One-line scalar preview for a tool input: first short string found. */
function previewInput(input: unknown, depth = 0): string | null {
  if (typeof input === "string") {
    const oneLine = input.split("\n")[0].trim();
    return oneLine ? oneLine.slice(0, 120) : null;
  }
  if (typeof input === "number" || typeof input === "boolean") return String(input);
  if (input !== null && typeof input === "object" && depth < 2) {
    for (const value of Object.values(input as Record<string, unknown>)) {
      const hit = previewInput(value, depth + 1);
      if (hit) return hit;
    }
  }
  return null;
}

function capText(value: unknown, limit = 2000): string {
  const s = typeof value === "string" ? value : JSON.stringify(value);
  if (s === undefined) return "";
  return s.length > limit ? `${s.slice(0, limit)}\n… (truncated, full output in artifacts)` : s;
}

/** Compact title + capped details for a tool_call step. Tool steps persist
 * as JSON blobs (`{tool, title, input, output, status}`); blobs truncated
 * at the backend cap are not valid JSON, so the fallback summarizes the
 * first line instead of dumping kilobytes into the collapsed title. */
export function summarizeToolCall(text: string | null | undefined): {
  title: string;
  details: string;
} {
  const raw = text ?? "";
  try {
    const data = JSON.parse(raw) as {
      tool?: unknown;
      title?: unknown;
      input?: unknown;
      output?: unknown;
      status?: unknown;
    };
    if (data === null || typeof data !== "object" || Array.isArray(data))
      throw new Error("not a tool blob");
    const tool = typeof data.tool === "string" && data.tool ? data.tool : "";
    const preview =
      (typeof data.title === "string" && data.title ? data.title.slice(0, 120) : null) ??
      previewInput(data.input);
    const title = tool && preview ? `${tool} — ${preview}` : tool || preview || "tool call";
    const details = JSON.stringify(
      {
        tool: data.tool ?? null,
        input: capText(data.input ?? null),
        output: capText(data.output ?? null),
        status: data.status ?? null,
      },
      null,
      2
    );
    return { title, details };
  } catch {
    const firstLine = raw.split("\n")[0].trim();
    return {
      title: firstLine.length > 120 ? `${firstLine.slice(0, 120)}…` : firstLine || "tool call",
      details: raw,
    };
  }
}
