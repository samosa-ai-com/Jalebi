import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Agents from "./Agents";

const AGENTS = [
  {
    id: "security-auditor",
    name: "Security Auditor",
    kind: "reviewer",
    cli: "opencode",
    model: "openai/gpt-5.1",
    personality_md: "Be adversarial.",
    skills: [{ name: "secure-coding", content: "# Secure coding\n" }],
    custom_instructions: "Check auth.",
    enabled: true,
    created_at: "2026-08-08T00:00:00",
  },
];

function makeFetchMock() {
  return vi.fn(async (url: string, init?: RequestInit) => {
    if (String(url).includes("/api/models")) {
      const models = String(url).includes("cli=codex")
        ? ["gpt-5.4-mini", "gpt-5.5"]
        : ["opencode-go/deepseek-v4-flash"];
      return { ok: true, json: async () => ({ cli: "opencode", models }) };
    }
    if (String(url).includes("/api/agents") && init?.method === "POST") {
      const body = JSON.parse(init.body as string) as Record<string, unknown>;
      return {
        ok: true,
        json: async () => ({ ...AGENTS[0], ...body }),
      };
    }
    if (String(url).includes("/api/agents") && init?.method === "PUT") {
      const body = JSON.parse(init.body as string) as Record<string, unknown>;
      return { ok: true, json: async () => ({ ...AGENTS[0], ...body }) };
    }
    if (String(url).includes("/api/agents")) {
      return { ok: true, json: async () => AGENTS };
    }
    return { ok: true, json: async () => [] };
  });
}

describe("Agents", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists catalog agents with their metadata", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    render(<Agents />);

    expect(await screen.findByText("security-auditor")).toBeInTheDocument();
    expect(screen.getByText("Security Auditor")).toBeInTheDocument();
    expect(screen.getByText("reviewer", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText(/model: openai\/gpt-5\.1/)).toBeInTheDocument();
    expect(screen.getByText(/1 skill\(s\)/)).toBeInTheDocument();
  });

  it("creates a new catalog agent", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    render(<Agents />);
    await screen.findByText("security-auditor");

    await userEvent.click(screen.getByRole("button", { name: "+ New agent" }));
    await userEvent.type(screen.getByPlaceholderText("security-auditor"), "docs-guru");
    await userEvent.type(screen.getByLabelText("Name"), "Docs Guru");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      const createCall = fetchMock.mock.calls.find(
        ([url, init]) => String(url).includes("/api/agents") && init?.method === "POST"
      );
      expect(createCall).toBeDefined();
      const body = JSON.parse((createCall?.[1] as RequestInit).body as string) as {
        id: string;
        name: string;
      };
      expect(body.id).toBe("docs-guru");
      expect(body.name).toBe("Docs Guru");
    });
  });

  it("model pin is a dropdown of models from /api/models, empty clears it", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    render(<Agents />);
    await screen.findByText("security-auditor");

    await userEvent.click(screen.getByRole("button", { name: "+ New agent" }));
    const modelSelect = screen.getByLabelText("Model pin (optional)") as HTMLSelectElement;
    expect(await screen.findByRole("option", { name: "no pin (CLI default)" })).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "opencode-go/deepseek-v4-flash" })
    ).toBeInTheDocument();

    // Picking a model sends it as the pin; picking the empty option clears it.
    await userEvent.selectOptions(modelSelect, "opencode-go/deepseek-v4-flash");
    expect(modelSelect.value).toBe("opencode-go/deepseek-v4-flash");
    await userEvent.selectOptions(modelSelect, "");
    expect(modelSelect.value).toBe("");
  });

  it("toggles an agent's enabled state via edit", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    render(<Agents />);
    await screen.findByText("security-auditor");

    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    const toggle = screen.getByRole("checkbox") as HTMLInputElement;
    await userEvent.click(toggle);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      const updateCall = fetchMock.mock.calls.find(
        ([url, init]) =>
          String(url).includes("/api/agents/security-auditor") && init?.method === "PUT"
      );
      expect(updateCall).toBeDefined();
    });
  });

  it("deletes an agent after confirmation", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    render(<Agents />);
    await screen.findByText("security-auditor");

    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      const deleteCall = fetchMock.mock.calls.find(
        ([url, init]) =>
          String(url).includes("/api/agents/security-auditor") && init?.method === "DELETE"
      );
      expect(deleteCall).toBeDefined();
    });
  });

  it("offers opencode/codex/claude in the CLI override select", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Agents />);
    await screen.findByText("security-auditor");
    await userEvent.click(screen.getByRole("button", { name: "+ New agent" }));

    const cliSelect = screen.getByLabelText("CLI override (optional)") as HTMLSelectElement;
    expect(cliSelect.value).toBe("");
    expect([...cliSelect.options].map((o) => o.value)).toEqual(
      expect.arrayContaining(["", "opencode", "codex", "claude"])
    );
  });

  it("refetches the Model dropdown when the CLI override changes", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Agents />);
    await screen.findByText("security-auditor");
    await userEvent.click(screen.getByRole("button", { name: "+ New agent" }));

    const model = screen.getByLabelText("Model pin (optional)") as HTMLSelectElement;
    expect([...model.options].map((o) => o.value)).toEqual(
      expect.arrayContaining(["opencode-go/deepseek-v4-flash"])
    );

    await userEvent.selectOptions(
      screen.getByLabelText("CLI override (optional)"),
      "codex"
    );
    await waitFor(() => {
      expect([...model.options].map((o) => o.value)).toEqual(
        expect.arrayContaining(["gpt-5.4-mini", "gpt-5.5"])
      );
    });
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("cli=codex"))).toBe(true);
  });
});
