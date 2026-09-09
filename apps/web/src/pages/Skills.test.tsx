import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Skills from "./Skills";

const SKILLS = [
  {
    id: "secure-coding",
    name: "Secure Coding",
    description: "Security checklist for any codebase.",
    content: "# Secure coding\nNever use eval.",
    tags: ["security", "review"],
    created_at: "2026-08-08T00:00:00",
    updated_at: "2026-08-10T00:00:00",
  },
  {
    id: "git-workflow",
    name: "Git Workflow",
    description: "Commit discipline and branch hygiene.",
    content: "# Git\nSmall commits.",
    tags: ["git"],
    created_at: "2026-08-08T00:00:00",
    updated_at: "2026-08-09T00:00:00",
  },
];

const USAGE: Record<string, { agents: { id: string; name: string }[] }> = {
  "secure-coding": { agents: [{ id: "auditor", name: "Auditor" }] },
  "git-workflow": { agents: [] },
};

function makeFetchMock(skills: unknown[] = SKILLS) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    if (String(url).includes("/usage")) {
      const slug = String(url).split("/api/skills/")[1]?.split("/")[0];
      return { ok: true, json: async () => USAGE[slug] ?? { agents: [] } };
    }
    if (String(url).includes("/api/skills") && init?.method === "POST") {
      const body = JSON.parse(init.body as string) as Record<string, unknown>;
      return { ok: true, json: async () => ({ ...SKILLS[0], ...body }) };
    }
    if (String(url).includes("/api/skills") && init?.method === "PUT") {
      const body = JSON.parse(init.body as string) as Record<string, unknown>;
      return { ok: true, json: async () => ({ ...SKILLS[0], ...body }) };
    }
    if (String(url).includes("/api/skills")) {
      return { ok: true, json: async () => skills };
    }
    return { ok: true, json: async () => [] };
  });
}

describe("Skills", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists skills with tags and usage", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    render(<Skills />);

    expect(await screen.findByText("secure-coding")).toBeInTheDocument();
    expect(screen.getByText("Git Workflow")).toBeInTheDocument();
    expect(await screen.findByText(/Linked by 1 agent: auditor/)).toBeInTheDocument();
    expect(screen.getByText("Not linked by any agent.")).toBeInTheDocument();
  });

  it("searches across id, name, description, and content", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    render(<Skills />);
    await screen.findByText("secure-coding");

    await userEvent.type(screen.getByPlaceholderText(/Search/), "git");
    expect(screen.queryByText("secure-coding")).not.toBeInTheDocument();
    expect(screen.getByText("git-workflow")).toBeInTheDocument();

    await userEvent.clear(screen.getByPlaceholderText(/Search/));
    await userEvent.type(screen.getByPlaceholderText(/Search/), "never use eval");
    expect(screen.getByText("secure-coding")).toBeInTheDocument();
  });

  it("filters by tag chips", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    render(<Skills />);
    await screen.findByText("secure-coding");

    await userEvent.click(screen.getByRole("button", { name: "git" }));
    expect(screen.queryByText("secure-coding")).not.toBeInTheDocument();
    expect(screen.getByText("git-workflow")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Clear tag" }));
    expect(screen.getByText("secure-coding")).toBeInTheDocument();
  });

  it("sorts by most used", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    render(<Skills />);
    await screen.findByText(/Linked by 1 agent/);

    await userEvent.click(screen.getByLabelText("Sort skills"));
    await userEvent.type(screen.getByRole("combobox"), "most used");
    await userEvent.click(screen.getByRole("option", { name: "Sort: most used" }));
    const cards = screen.getAllByText(/secure-coding|git-workflow/, { exact: false });
    expect(cards[0].textContent).toContain("secure-coding");
  });

  it("creates a new skill with tags", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Skills />);
    await screen.findByText("secure-coding");

    await userEvent.click(screen.getByRole("button", { name: "+ New skill" }));
    await userEvent.type(screen.getByPlaceholderText("secure-coding"), "api-design");
    await userEvent.type(screen.getByLabelText("Name"), "API Design");
    await userEvent.type(screen.getByLabelText("Tags (comma-separated)"), "api, design");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([url, init]) => String(url).includes("/api/skills") && init?.method === "POST"
      );
      expect(call).toBeDefined();
      expect(JSON.parse((call?.[1] as RequestInit).body as string)).toMatchObject({
        id: "api-design",
        name: "API Design",
        tags: ["api", "design"],
      });
    });
  });

  it("refuses bad slugs before saving", async () => {
    const fetchMock = makeFetchMock();
    vi.stubGlobal("fetch", fetchMock);
    render(<Skills />);
    await screen.findByText("secure-coding");

    await userEvent.click(screen.getByRole("button", { name: "+ New skill" }));
    await userEvent.type(screen.getByPlaceholderText("secure-coding"), "Bad Slug!");
    await userEvent.type(screen.getByLabelText("Name"), "Bad");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/lowercase letters\/digits/)).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.filter(
        ([url, init]) => String(url).includes("/api/skills") && init?.method === "POST"
      )
    ).toHaveLength(0);
  });

  it("delete confirmation names the linked agents", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    vi.stubGlobal("fetch", makeFetchMock());
    render(<Skills />);
    await screen.findByText(/Linked by 1 agent/);

    const deletes = await screen.findAllByRole("button", { name: "Delete" });
    // Cards sort by name: git-workflow first, secure-coding (linked) second.
    await userEvent.click(deletes[1]);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(String(confirm.mock.calls[0][0])).toContain("auditor");
  });

  it("switching edits between skills shows the newly edited skill", async () => {
    vi.stubGlobal("fetch", makeFetchMock());
    render(<Skills />);
    await screen.findByText("secure-coding");

    const edits = await screen.findAllByRole("button", { name: "Edit" });
    // Cards sort by name: git-workflow first, secure-coding second.
    await userEvent.click(edits[1]);
    expect(await screen.findByDisplayValue("Secure Coding")).toBeInTheDocument();
    await userEvent.click(edits[0]);
    expect(await screen.findByDisplayValue("Git Workflow")).toBeInTheDocument();
  });
});
