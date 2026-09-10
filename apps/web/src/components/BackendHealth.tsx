import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { BackendHealth as HealthRow } from "../types";

/** Per-backend install/version drift check (read-only, from
 * `GET /api/backends/health`). A drift only warns — newer CLIs usually
 * still work, and parsers degrade to verbatim text rather than crashing. */
export default function BackendHealth() {
  const [rows, setRows] = useState<HealthRow[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.getBackendsHealth().then(
      (r) => {
        if (!cancelled) setRows(Array.isArray(r?.backends) ? r.backends : []);
      },
      () => {
        if (!cancelled) setFailed(true);
      }
    );
    return () => {
      cancelled = true;
    };
  }, []);

  if (failed) return <p className="text-xs text-ink-500">Backend health unavailable.</p>;
  if (rows === null) return <p className="text-xs text-ink-500">Checking backends…</p>;
  return (
    <ul className="w-full space-y-1">
      {rows.map((b) => (
        <li key={b.cli} className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">
          <span
            aria-hidden
            className={`inline-block h-1.5 w-1.5 rounded-full ${
              b.installed ? "bg-green-400" : "bg-red-400"
            }`}
          />
          <span className="font-mono text-ink-200">{b.cli}</span>
          <span className="text-ink-500">
            {b.installed ? (b.version ?? "installed (unknown version)") : "not installed"}
          </span>
          {b.installed && !b.version_match && (
            <span className="text-amber-300">
              differs from verified {b.verified ?? "unknown"} — report parsing oddities
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}
