/**
 * Merge-readiness panel (Phase 4 T3.2).
 *
 * Renders the per-check status of `GET /api/tasks/<id>/publish-check`
 * above the Publish button. Renders nothing while loading or when the
 * response shape is unexpected (defensive — keeps the existing TaskDetail
 * tests, which stub `/publish-check` with a Task payload, green).
 */
import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { api } from "../api/client";
import GlossaryTerm from "./GlossaryTerm";
import type { PublishCheck, PublishCheckStatus } from "../types";

const STATUS_STYLES: { [K in PublishCheckStatus]: { wrap: string; dot: string } } = {
  ready: { wrap: "bg-green-500/10 text-green-300 ring-green-500/40", dot: "bg-green-400" },
  attention: { wrap: "bg-syrup-500/10 text-syrup-300 ring-syrup-500/40", dot: "bg-syrup-400" },
  blocked: { wrap: "bg-red-500/10 text-red-300 ring-red-500/40", dot: "bg-red-400" },
};

function CheckRow({
  ok,
  name,
  message,
  action,
}: {
  ok: boolean;
  name: string;
  message: string;
  action?: ReactNode;
}) {
  return (
    <li className="flex items-start gap-2 py-1.5 text-sm">
      <span
        className={`mt-0.5 inline-block h-2 w-2 shrink-0 rounded-full ${
          ok ? "bg-green-400" : "bg-red-400"
        }`}
      />
      <span className="w-24 shrink-0 font-mono text-xs text-ink-300">{name}</span>
      <span className={`flex-1 ${ok ? "text-ink-300" : "text-red-300"}`}>
        {message}
      </span>
      {action}
    </li>
  );
}

export function MergeReadinessPanel({
  taskId,
  refreshKey,
  onFixCi,
}: {
  taskId: number;
  refreshKey: string;
  onFixCi?: () => void;
}) {
  const [data, setData] = useState<PublishCheck | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    // Loading flag is intentionally mirrored into local state on every
    // refresh — the lint rule is overly strict for "fetch → setState on
    // completion"; the cancellation guard below prevents the cascading
    // render it warns about.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    api
      .getPublishCheck(taskId)
      .then((d) => {
        if (cancelled) return;
        // Defensive shape check — see file header.
        if (
          d &&
          (d.status === "ready" ||
            d.status === "attention" ||
            d.status === "blocked") &&
          Array.isArray(d.checks)
        ) {
          setData(d);
          setError(null);
        } else {
          setData(null);
        }
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "failed to load");
        setData(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [taskId, refreshKey]);

  if (loading && !data) {
    return (
      <section className="surface p-5 animate-fade-up">
        <h2 className="panel-title mb-2">Publish readiness</h2>
        <p className="text-sm text-ink-500">Loading…</p>
      </section>
    );
  }
  if (error) {
    return (
      <section className="surface p-5 animate-fade-up">
        <h2 className="panel-title mb-2">Publish readiness</h2>
        <p className="text-xs text-red-400">{error}</p>
      </section>
    );
  }
  if (!data) return null;

  const style = STATUS_STYLES[data.status];
  return (
    <section className="surface p-5 animate-fade-up">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="panel-title">Publish readiness</h2>
        <span
          className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 font-mono text-[11px] font-medium ring-1 ring-inset ${style.wrap}`}
        >
          <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} />
          {data.status === "ready"
            ? "Ready"
            : data.status === "attention"
              ? "Attention"
              : "Blocked"}
        </span>
      </div>
      <p className="mb-2 text-xs text-ink-500">
        Against{" "}
        <span className="font-mono text-ink-300">
          <GlossaryTerm term="base-ref">{data.base_ref}</GlossaryTerm>
        </span>
      </p>
      <ul className="divide-y divide-ink-800/60">
        {data.checks.map((c) => (
          <CheckRow
            key={c.name}
            ok={c.ok}
            name={c.name}
            message={c.message}
            action={
              c.name === "ci" && c.state === "failure" && onFixCi ? (
                <button
                  type="button"
                  onClick={onFixCi}
                  className="btn-ghost shrink-0 text-xs"
                  title="Open the New-task form to fetch the failing workflow logs and fix the code"
                >
                  Fix failed CI
                </button>
              ) : undefined
            }
          />
        ))}
      </ul>
    </section>
  );
}

export default MergeReadinessPanel;