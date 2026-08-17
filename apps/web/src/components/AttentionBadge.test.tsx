/** Phase 4 T3.1 — AttentionBadge (one render test per attention value). */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { AttentionBadge } from "./AttentionBadge";
import type { AttentionValue } from "../types";

describe("AttentionBadge", () => {
  const VALUES: AttentionValue[] = [
    "needs_you",
    "working",
    "in_review",
    "ready_to_merge",
    "done",
  ];
  for (const v of VALUES) {
    it(`renders ${v} with the expected label`, () => {
      render(<AttentionBadge attention={v} />);
      const expectedLabel = v.replace(/_/g, " ");
      expect(screen.getByText(expectedLabel)).toBeInTheDocument();
    });
  }

  it("uses a pulsing dot for needs_you (high-signal)", () => {
    const { container } = render(<AttentionBadge attention="needs_you" />);
    expect(container.querySelector(".animate-pulse-dot")).not.toBeNull();
  });

  it("uses a muted dot for done", () => {
    const { container } = render(<AttentionBadge attention="done" />);
    expect(container.querySelector(".animate-pulse-dot")).toBeNull();
  });
});