/**
 * Phase 4 T7 — in-worktree file browser/viewer (read-only).
 *
 * Lists the task's worktree files, navigates folders, and views text
 * files inline (markdown rendered via <Markdown>, code/JSON in a mono
 * pre; binary flagged for the artifact download flow). Path safety is
 * enforced server-side (`workspace_files`); this component never
 * constructs absolute paths.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../api/client";
import Markdown from "./Markdown";
import type { FileEntry } from "../types";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

const MD_EXTS = new Set(["md", "markdown"]);

function FileBrowser({
  taskId,
  refreshSignal,
  onOpenInIde,
  ideName,
}: {
  taskId: number;
  refreshSignal?: number;
  onOpenInIde?: () => void;
  ideName?: string | null;
}) {
  const [path, setPath] = useState("");
  const [entries, setEntries] = useState<FileEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [openFile, setOpenFile] = useState<{
    path: string;
    name: string;
    content: string;
    binary: boolean;
  } | null>(null);

  const load = useCallback(
    (p: string) => {
      setLoading(true);
      setError(null);
      api
        .getTaskFiles(taskId, p)
        .then((r) => setEntries(Array.isArray(r.entries) ? r.entries : []))
        .catch((e) => setError(e instanceof Error ? e.message : "failed to list files"))
        .finally(() => setLoading(false));
    },
    [taskId]
  );

  useEffect(() => {
    // load() calls setState (loading/entries) — fetch-driven state sync.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load(path);
  }, [load, path]);

  // Live refresh on SSE (debounced) so new files appear as the agent works,
  // including the final state when the run completes (refreshSignal drops
  // back to 0 when the live buffer clears — a change like any other, not
  // a reason to skip). The mount-time signal is ignored (initial load
  // above already fetched).
  const signalSeenRef = useRef(false);
  useEffect(() => {
    if (!signalSeenRef.current) {
      signalSeenRef.current = true;
      return;
    }
    const t = setTimeout(() => {
      if (!openFile) load(path);
    }, 800);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshSignal]);

  const open = (entry: FileEntry) => {
    api
      .getTaskFileContent(taskId, entry.path)
      .then((r) =>
        setOpenFile({ path: entry.path, name: entry.name, content: r.content, binary: r.binary })
      )
      .catch((e) => setError(e instanceof Error ? e.message : "failed to read file"));
  };

  const segments = path ? path.split("/") : [];
  const crumb: { label: string; path: string }[] = [
    { label: "/", path: "" },
    ...segments.map((seg, i) => ({
      label: seg,
      path: segments.slice(0, i + 1).join("/"),
    })),
  ];

  return (
    <section className="surface p-5 animate-fade-up">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2.5">
          <h2 className="panel-title">Files</h2>
          {entries !== null && (
            <span className="text-xs text-ink-500">
              {entries.filter((e) => !e.is_dir).length} file
              {entries.filter((e) => !e.is_dir).length === 1 ? "" : "s"}
            </span>
          )}
        </div>
        {onOpenInIde && (
          <button
            type="button"
            onClick={onOpenInIde}
            className="inline-flex items-center gap-1.5 rounded-md border border-ink-700/60 bg-ink-900/60 px-2 py-1 text-xs text-ink-300 transition-colors hover:border-syrup-500/50 hover:bg-ink-800 hover:text-syrup-300"
            title={`Open worktree in ${ideName || "configured IDE"}`}
          >
            <svg className="h-3 w-3 text-syrup-400" viewBox="0 0 16 16" fill="currentColor">
              <path d="M2 3.75C2 2.784 2.784 2 3.75 2h8.5c.966 0 1.75.784 1.75 1.75v8.5A1.75 1.75 0 0 1 12.25 14h-8.5A1.75 1.75 0 0 1 2 12.25Zm1.75-.25a.25.25 0 0 0-.25.25v8.5c0 .138.112.25.25.25h8.5a.25.25 0 0 0 .25-.25v-8.5a.25.25 0 0 0-.25-.25Z" />
              <path d="M5.78 5.47a.75.75 0 0 1 0 1.06L4.81 7.5l.97.97a.75.75 0 1 1-1.06 1.06l-1.5-1.5a.75.75 0 0 1 0-1.06l1.5-1.5a.75.75 0 0 1 1.06 0Zm4.44 0a.75.75 0 0 1 1.06 0l1.5 1.5a.75.75 0 0 1 0 1.06l-1.5 1.5a.75.75 0 0 1-1.06-1.06l.97-.97-.97-.97a.75.75 0 0 1 0-1.06Z" />
            </svg>
            <span>Open in {ideName || "IDE"}</span>
          </button>
        )}
      </div>

      <div className="mb-2 flex flex-wrap items-center gap-1 text-xs">
        {crumb.map((c, i) => (
          <span key={`${c.path}-${i}`} className="flex items-center gap-1">
            {i > 0 && <span className="text-ink-700">/</span>}
            <button
              type="button"
              onClick={() => {
                setPath(c.path);
                setOpenFile(null);
              }}
              className={`font-mono transition-colors ${
                c.path === path ? "text-syrup-300" : "text-ink-400 hover:text-ink-200"
              }`}
            >
              {c.label}
            </button>
          </span>
        ))}
      </div>

      {error && <p className="mb-2 text-xs text-red-400">{error}</p>}
      {loading && entries === null && <p className="text-sm text-ink-500">Loading…</p>}

      {openFile ? (
        <div>
          <div className="mb-2 flex items-center justify-between">
            <span className="font-mono text-xs text-ink-300">{openFile.name}</span>
            <button
              type="button"
              onClick={() => setOpenFile(null)}
              className="btn-ghost !px-2 !py-1 text-xs"
            >
              Close
            </button>
          </div>
          {openFile.binary ? (
            <p className="rounded bg-ink-900/60 p-3 font-mono text-[11px] text-ink-400">
              binary file — use Artifacts below to download.
            </p>
          ) : MD_EXTS.has(openFile.name.split(".").pop()?.toLowerCase() ?? "") ? (
            <div className="max-h-[24rem] overflow-auto rounded bg-ink-900/40 p-3">
              <Markdown>{openFile.content}</Markdown>
            </div>
          ) : (
            <pre className="max-h-[24rem] overflow-auto whitespace-pre-wrap rounded bg-ink-900/60 p-3 font-mono text-[11px] leading-relaxed text-ink-200">
              {openFile.content}
            </pre>
          )}
        </div>
      ) : (
        <ul className="divide-y divide-ink-800/60">
          {entries === null || entries.length === 0 ? (
            <li className="py-6 text-center text-sm text-ink-600">No files here.</li>
          ) : (
            entries.map((entry) => (
              <li key={entry.path}>
                <button
                  type="button"
                  onClick={() => (entry.is_dir ? setPath(entry.path) : open(entry))}
                  className="flex w-full items-center gap-3 px-2 py-2 text-left transition-colors hover:bg-ink-850/40"
                >
                  <span className={`font-mono ${entry.is_dir ? "text-syrup-300" : "text-ink-400"}`}>
                    {entry.is_dir ? "📁" : "📄"}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-sm text-ink-200">{entry.name}</span>
                  {!entry.is_dir && (
                    <>
                      <span className="shrink-0 font-mono text-[11px] text-ink-500">
                        {formatBytes(entry.size)}
                      </span>
                      {entry.extension && (
                        <span className="shrink-0 rounded bg-ink-800/60 px-1.5 py-0.5 font-mono text-[10px] text-ink-400">
                          {entry.extension}
                        </span>
                      )}
                    </>
                  )}
                </button>
              </li>
            ))
          )}
        </ul>
      )}
    </section>
  );
}

export default FileBrowser;
