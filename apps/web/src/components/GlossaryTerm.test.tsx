import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import GlossaryTerm from "./GlossaryTerm";
import { GLOSSARY } from "../lib/glossary";

describe("GlossaryTerm", () => {
  it("renders children with the definition as a native tooltip", () => {
    render(<GlossaryTerm term="backend">Backend</GlossaryTerm>);
    const el = screen.getByText("Backend");
    expect(el.tagName).toBe("SPAN");
    expect(el).toHaveAttribute("title", GLOSSARY.backend);
    expect(el.className).toMatch(/decoration-dotted/);
  });

  it("is keyboard-focusable with the definition exposed to screen readers", () => {
    render(<GlossaryTerm term="backend">Backend</GlossaryTerm>);
    const el = screen.getByText("Backend");
    expect(el).toHaveAttribute("tabindex", "0");
    const describedBy = el.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    const description = document.getElementById(describedBy!);
    expect(description?.textContent).toBe(GLOSSARY.backend);
  });

  it("renders plain children for an unknown term", () => {
    render(<GlossaryTerm term="nope">Mystery</GlossaryTerm>);
    const el = screen.getByText("Mystery");
    expect(el).not.toHaveAttribute("title");
  });

  it("covers every glossary key with non-empty text", () => {
    for (const [term, definition] of Object.entries(GLOSSARY)) {
      expect(term.trim()).not.toBe("");
      expect(definition.trim()).not.toBe("");
    }
    expect(Object.keys(GLOSSARY).length).toBeGreaterThanOrEqual(10);
  });
});
