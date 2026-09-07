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
    skill_ids: ["secure-coding"],
    custom_instructions: "Check auth.",
    enabled: true,
    description: "Finds vulns and bad practices.",
    avatar: "shield",
    created_at: "2026-08-08T00:00:00",
  },
];

const LIBRARY = [
  {
    id: "secure-coding",
    name: "Secure Coding",
    description: "Security checklist for any codebase.",
    content: "# Secure coding\n",
    tags: ["security"],
    created_at: "2026-08-08T00:00:00",
    updated_at: "2026-08-08T00:00:00",
  },
];

const AGENTS_TWO = [
  AGENTS[0],
  {
    id: "docs-guru",
    name: "Docs Guru",
    kind: "general",
    cli: null,
    model: null,
    personality_md: "",
    skills: [],
    custom_instructions: "",
    enabled: false,
    created_at: "2026-08-09T00:00:00",
  },
];

const USAGE = {
  task_count: 2,
  trigger_rules: [
    {
      id: 3,
      event: "pull_request.opened",
      action: "start_review",
      repo_id: 1,
      repo_full_name: "owner/repo",
    },
  ],
};

function makeFetchMock(agents: unknown[] = AGENTS) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    if (String(url).includes("/api/models")) {
      const models = String(url).includes("cli=codex")
        ? ["gpt-5.4-mini", "gpt-5.5"]
        : ["opencode-go/deepseek-v4-flash"];
      return { ok: true, json: async () => ({ cli: "opencode", models }) };
    }
    if (String(url).includes("/usage")) {
      return { ok: true, json: async () => USAGE };
    }
    if (String(url) === "/api/skills" || String(url).endsWith("/api/skills")) {
      return { ok: true, json: async () => LIBRARY };
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
      return { ok: true, json: async () => agents };
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
    const toggle = screen.getByRole("checkbox", {
      name: /enabled/i,
    }) as HTMLInputElement;
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

    await userEvent.selectOptions(screen.getByLabelText("CLI override (optional)"), "codex");
    await waitFor(() => {
      expect([...model.options].map((o) => o.value)).toEqual(
        expect.arrayContaining(["gpt-5.4-mini", "gpt-5.5"])
      );
    });
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("cli=codex"))).toBe(true);
  });

  it("shows usage on rows and toggles enabled without the form", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Agents />);
    await screen.findByText("security-auditor");

    expect(await screen.findByText(/Used by 2 tasks/)).toBeInTheDocument();
    expect(screen.getByText(/1 trigger rule/, { exact: false })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Disable" }));
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([url, init]) =>
          String(url).includes("/api/agents/security-auditor") && init?.method === "PUT"
      );
      expect(call).toBeDefined();
      expect(JSON.parse((call?.[1] as RequestInit).body as string)).toMatchObject({
        enabled: false,
      });
    });
  });

  it("switching edits between agents shows the newly edited agent", async () => {
    vi.stubGlobal("fetch", makeFetchMock(AGENTS_TWO));
    render(<Agents />);
    await screen.findByText("security-auditor");

    const edits = await screen.findAllByRole("button", { name: "Edit" });
    expect(edits).toHaveLength(2);
    // Sorted by name: Docs Guru first, Security Auditor second.
    await userEvent.click(edits[0]);
    expect(await screen.findByDisplayValue("Docs Guru")).toBeInTheDocument();
    await userEvent.click(edits[1]);
    // Without the key-remount fix the form would still show the first agent.
    expect(await screen.findByDisplayValue("Security Auditor")).toBeInTheDocument();
  });

  it("refuses bad slugs before saving", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Agents />);
    await screen.findByText("security-auditor");

    await userEvent.click(screen.getByRole("button", { name: "+ New agent" }));
    await userEvent.type(screen.getByPlaceholderText("security-auditor"), "Bad Slug!");
    await userEvent.type(screen.getByLabelText("Name"), "Bad");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/lowercase letters\/digits/)).toBeInTheDocument();

    expect(
      fetchMock.mock.calls.filter(
        ([url, init]) => String(url).includes("/api/agents") && init?.method === "POST"
      )
    ).toHaveLength(0);
  });

  it("clears the model pin when the CLI override changes", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    render(<Agents />);
    await screen.findByText("security-auditor");

    await userEvent.click(screen.getByRole("button", { name: "+ New agent" }));
    const model = screen.getByLabelText("Model pin (optional)") as HTMLSelectElement;
    await userEvent.selectOptions(model, "opencode-go/deepseek-v4-flash");
    expect(model.value).toBe("opencode-go/deepseek-v4-flash");
    await userEvent.selectOptions(screen.getByLabelText("CLI override (optional)"), "codex");
    await waitFor(() => expect(model.value).toBe(""));
  });

  it("delete confirmation names the impacted trigger rules", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    vi.stubGlobal("fetch", makeFetchMock());
    render(<Agents />);
    await screen.findByText("security-auditor");
    await screen.findByText(/Used by 2 tasks/);

    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(confirm).toHaveBeenCalledTimes(1);
    const message = String(confirm.mock.calls[0][0]);
    expect(message).toContain("WARNING");
    expect(message).toContain("#3 pull_request.opened");
  });

  it("rows show the avatar and description", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    render(<Agents />);
    await screen.findByText("security-auditor");

    expect(screen.getByText("Finds vulns and bad practices.")).toBeInTheDocument();
    const img = screen.getByTitle("avatar: shield");
    expect(img).toHaveAttribute("src", "/avatars/shield.svg");
  });

  it("suggests an avatar from the name and attaches library skills", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Agents />);
    await screen.findByText("security-auditor");

    await userEvent.click(screen.getByRole("button", { name: "+ New agent" }));
    await userEvent.type(screen.getByPlaceholderText("security-auditor"), "test-guru");
    await userEvent.type(screen.getByLabelText("Name"), "Test Guru");
    // Keyword match on the name suggests the flask avatar for "Auto".
    expect(await screen.findByText(/auto-suggested.*flask/)).toBeInTheDocument();

    const box = await screen.findByRole("checkbox", { name: /secure-coding/ });
    await userEvent.click(box);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([url, init]) => String(url).includes("/api/agents") && init?.method === "POST"
      );
      expect(call).toBeDefined();
      expect(JSON.parse((call?.[1] as RequestInit).body as string)).toMatchObject({
        id: "test-guru",
        skill_ids: ["secure-coding"],
        skills: [],
        avatar: null,
      });
    });
  });

  it("an explicit avatar pick is sent", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Agents />);
    await screen.findByText("security-auditor");

    await userEvent.click(screen.getByRole("button", { name: "+ New agent" }));
    await userEvent.type(screen.getByPlaceholderText("security-auditor"), "doc-helper");
    await userEvent.type(screen.getByLabelText("Name"), "Doc Helper");
    await userEvent.click(screen.getByTitle("Rocket"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([url, init]) => String(url).includes("/api/agents") && init?.method === "POST"
      );
      expect(call).toBeDefined();
      expect(JSON.parse((call?.[1] as RequestInit).body as string)).toMatchObject({
        avatar: "rocket",
      });
    });
  });

  it("searches the agent list by id, name, and description", async () => {
    vi.stubGlobal("fetch", makeFetchMock(AGENTS_TWO));
    render(<Agents />);
    await screen.findByText("security-auditor");
    expect(screen.getByText("docs-guru")).toBeInTheDocument();

    await userEvent.type(screen.getByPlaceholderText(/Search id, name/), "docs");
    expect(screen.queryByText("security-auditor")).not.toBeInTheDocument();
    expect(screen.getByText("docs-guru")).toBeInTheDocument();

    await userEvent.clear(screen.getByPlaceholderText(/Search id, name/));
    // Description match (the auditor's description mentions vulns).
    await userEvent.type(screen.getByPlaceholderText(/Search id, name/), "vulns");
    expect(screen.getByText("security-auditor")).toBeInTheDocument();
    expect(screen.queryByText("docs-guru")).not.toBeInTheDocument();
  });

  it("filters by kind and status pills", async () => {
    vi.stubGlobal("fetch", makeFetchMock(AGENTS_TWO));
    render(<Agents />);
    await screen.findByText("security-auditor");

    // AGENTS_TWO: auditor = reviewer/enabled, guru = general/disabled.
    await userEvent.click(screen.getByRole("button", { name: "general" }));
    expect(screen.queryByText("security-auditor")).not.toBeInTheDocument();
    expect(screen.getByText("docs-guru")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "All kinds" }));
    await userEvent.click(screen.getByRole("button", { name: "disabled" }));
    expect(screen.queryByText("security-auditor")).not.toBeInTheDocument();
    expect(screen.getByText("docs-guru")).toBeInTheDocument();
  });

  it("sorts newest-first", async () => {
    vi.stubGlobal("fetch", makeFetchMock(AGENTS_TWO));
    render(<Agents />);
    await screen.findByText("security-auditor");

    await userEvent.selectOptions(screen.getByLabelText("Sort agents"), "newest");
    const rows = screen.getAllByText(/security-auditor|docs-guru/, { exact: false });
    // docs-guru (08-09) is newer than security-auditor (08-08).
    expect(rows[0].textContent).toContain("docs-guru");
  });

  it("the skill picker searches the library", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    render(<Agents />);
    await screen.findByText("security-auditor");

    await userEvent.click(screen.getByRole("button", { name: "+ New agent" }));
    expect(await screen.findByText("Secure Coding")).toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText("Search skills…"), "no-such-skill");
    expect(screen.queryByText("Secure Coding")).not.toBeInTheDocument();
  });

  it("saving an edit drops legacy inline skills", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Agents />);
    await screen.findByText("security-auditor");

    // The mock agent carries one inline skill, but the form offers no editor:
    // saving clears it (skills live in the library now).
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.queryByPlaceholderText(/skill name/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([url, init]) =>
          String(url).includes("/api/agents/security-auditor") && init?.method === "PUT"
      );
      expect(call).toBeDefined();
      expect(JSON.parse((call?.[1] as RequestInit).body as string)).toMatchObject({
        skills: [],
      });
    });
  });
});
