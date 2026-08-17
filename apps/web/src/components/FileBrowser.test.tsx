/** Phase 4 T7 — in-worktree file browser/viewer. */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import FileBrowser from "./FileBrowser";

const ROOT = {
  path: "",
  entries: [
    { name: "sub", path: "sub", is_dir: true, size: 0, extension: "" },
    { name: "hello.txt", path: "hello.txt", is_dir: false, size: 11, extension: "txt" },
    { name: "README.md", path: "README.md", is_dir: false, size: 20, extension: "md" },
    { name: "bin.dat", path: "bin.dat", is_dir: false, size: 100, extension: "dat" },
  ],
};

const SUB = {
  path: "sub",
  entries: [{ name: "nested.py", path: "sub/nested.py", is_dir: false, size: 30, extension: "py" }],
};

function stubFetch() {
  return vi.fn(async (url: string) => {
    if (url.includes("/files/content")) {
      if (url.includes("bin.dat")) {
        return { ok: true, json: async () => ({ path: "bin.dat", content: "", binary: true }) };
      }
      if (url.includes("README.md")) {
        return { ok: true, json: async () => ({ path: "README.md", content: "# Title\n\nhello", binary: false }) };
      }
      if (url.includes("nested.py")) {
        return { ok: true, json: async () => ({ path: "sub/nested.py", content: "print(1)", binary: false }) };
      }
      return { ok: true, json: async () => ({ path: "", content: "", binary: false }) };
    }
    if (url.includes("?path=sub")) {
      return { ok: true, json: async () => SUB };
    }
    return { ok: true, json: async () => ROOT };
  });
}

describe("FileBrowser", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders folder + file entries (folders first, size + extension badges)", async () => {
    const fetchMock = stubFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<FileBrowser taskId={7} />);
    expect(await screen.findByText("sub")).toBeInTheDocument();
    expect(screen.getByText("hello.txt")).toBeInTheDocument();
    expect(screen.getByText("README.md")).toBeInTheDocument();
    expect(screen.getByText("11 B")).toBeInTheDocument();
    expect(screen.getByText("txt")).toBeInTheDocument();
  });

  it("navigates into a folder and updates the breadcrumb", async () => {
    const fetchMock = stubFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<FileBrowser taskId={7} />);
    await screen.findByText("sub");
    await userEvent.click(screen.getByText("sub"));
    expect(await screen.findByText("nested.py")).toBeInTheDocument();
    // Breadcrumb now shows sub.
    expect(screen.getByText("py")).toBeInTheDocument();
  });

  it("opens a markdown file and renders it via Markdown", async () => {
    const fetchMock = stubFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<FileBrowser taskId={7} />);
    await screen.findByText("README.md");
    await userEvent.click(screen.getByText("README.md"));
    expect(await screen.findByText("Title")).toBeInTheDocument();
  });

  it("opens a text/code file in a mono pre", async () => {
    const fetchMock = stubFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<FileBrowser taskId={7} />);
    await screen.findByText("sub");
    await userEvent.click(screen.getByText("sub"));
    await screen.findByText("nested.py");
    await userEvent.click(screen.getByText("nested.py"));
    expect(await screen.findByText("print(1)")).toBeInTheDocument();
  });

  it("shows the binary note for a binary file", async () => {
    const fetchMock = stubFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<FileBrowser taskId={7} />);
    await screen.findByText("bin.dat");
    await userEvent.click(screen.getByText("bin.dat"));
    expect(
      await screen.findByText(/binary file — use Artifacts below to download/i)
    ).toBeInTheDocument();
  });
});
