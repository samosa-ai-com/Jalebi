import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import SearchableSelect from "./SearchableSelect";

function Harness({
  initial = "",
  options = ["opencode", "codex", "claude"],
  allowCustom = false,
  placeholder = "Pick one",
}: {
  initial?: string;
  options?: string[];
  allowCustom?: boolean;
  placeholder?: string;
}) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <SearchableSelect
        label="Backend"
        value={value}
        onChange={setValue}
        options={options}
        placeholder={placeholder}
        allowCustom={allowCustom}
      />
      <output data-testid="value">{value}</output>
    </>
  );
}

describe("SearchableSelect", () => {
  it("selects an option by search + click", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Backend" }));
    await user.type(screen.getByRole("combobox", { name: "Search Backend" }), "codex");
    expect(screen.getByRole("option", { name: "codex" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "opencode" })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "claude" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("option", { name: "codex" }));
    expect(screen.getByTestId("value")).toHaveTextContent("codex");
  });

  it("supports keyboard navigation and Escape", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Backend" }));
    const box = screen.getByRole("combobox", { name: "Search Backend" });
    await user.click(box);
    await user.type(box, "c");
    await user.keyboard("{ArrowDown}{Enter}");
    expect(screen.getByTestId("value")).toHaveTextContent("codex");
    await user.click(screen.getByRole("button", { name: "Backend" }));
    await user.click(screen.getByRole("combobox", { name: "Search Backend" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("reaches the clear placeholder via ArrowUp", async () => {
    const user = userEvent.setup();
    render(<Harness initial="codex" />);
    await user.click(screen.getByRole("button", { name: "Backend" }));
    await user.click(screen.getByRole("combobox", { name: "Search Backend" }));
    await user.keyboard("{ArrowUp}{Enter}");
    expect(screen.getByTestId("value")).toHaveTextContent("");
  });

  it("keeps a value missing from the options visible with a warning", async () => {
    render(<Harness initial="custom-x" options={["a", "b"]} />);
    expect(screen.getByRole("button", { name: "Backend" })).toHaveTextContent("custom-x");
    expect(screen.getByText(/not in the known list/i)).toBeInTheDocument();
  });

  it("shows labelTitle as a tooltip while keeping the accessible name", () => {
    render(
      <>
        <SearchableSelect
          label="Backend"
          labelTitle="The AI coding assistant that runs the task."
          value=""
          onChange={() => {}}
          options={["opencode"]}
          placeholder="Pick one"
        />
      </>
    );
    // The toggle button's accessible name is still the plain label …
    expect(screen.getByRole("button", { name: "Backend" })).toBeInTheDocument();
    // … while the visible label carries the hint.
    const visible = screen.getByText("Backend", { selector: "span" });
    expect(visible).toHaveAttribute("title", "The AI coding assistant that runs the task.");
  });

  it("offers a custom value when allowCustom", async () => {
    const user = userEvent.setup();
    render(<Harness allowCustom options={[]} />);
    await user.click(screen.getByRole("button", { name: "Backend" }));
    await user.type(screen.getByRole("combobox", { name: "Search Backend" }), "my-model");
    await user.click(screen.getByRole("option", { name: /use custom value/i }));
    expect(screen.getByTestId("value")).toHaveTextContent("my-model");
  });
});
