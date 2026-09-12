import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { KitchenWire } from "./KitchenWire";

describe("KitchenWire", () => {
  it("satisfies the target size contract with min-h-6 on filter pills", () => {
    render(
      <KitchenWire
        tasks={[]}
        screens={[]}
        findings={[]}
        now={Date.now()}
      />
    );

    const filterPills = screen.getAllByRole("button");
    expect(filterPills.length).toBeGreaterThan(0);
    for (const pill of filterPills) {
      expect(pill.className).toMatch(/\bmin-h-6\b/);
    }
  });
});
