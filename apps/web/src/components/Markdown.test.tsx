import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Markdown from "./Markdown";

describe("Markdown", () => {
  it("renders heading, list, table, code fence and inline code", () => {
    const MD = `# Title\n\nSome text with \`inline code\`.\n\n- item one\n- item two\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n\`\`\`py\nprint("hi")\n\`\`\`\n`;
    const { container } = render(<Markdown>{MD}</Markdown>);

    expect(screen.getByRole("heading", { name: "Title" })).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);

    const table = screen.getByRole("table");
    expect(table).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();

    const pre = container.querySelector("pre");
    expect(pre).not.toBeNull();
    expect(pre).toHaveClass("bg-ink-900/60", "max-h-72");
    expect(screen.getByText('print("hi")')).toBeInTheDocument();

    const inline = screen.getByText("inline code");
    expect(inline.tagName).toBe("CODE");
    expect(inline).toHaveClass("bg-ink-850");
  });

  it("escapes raw html", () => {
    const { container } = render(<Markdown>{"<script>alert(1)</script>"}</Markdown>);
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText(/alert\(1\)/)).toBeInTheDocument();
  });
});
