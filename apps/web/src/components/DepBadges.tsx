import { Link } from "react-router-dom";

/** Phase 4 T4.1 — dependency badges for a task row/header.
 *
 * Renders nothing when the task has no dependency edges. Otherwise shows:
 * - an orange "blocked" pill when the backend-derived `blocked` flag is set
 *   (with the unmet `blocked_by` ids in the tooltip);
 * - "depends on #N" links for each upstream edge;
 * - "blocks #N" links for each downstream dependent.
 */
export function DepBadges({
  dependsOn,
  blockedBy,
  blocking,
  blocked,
}: {
  dependsOn?: number[];
  blockedBy?: number[];
  blocking?: number[];
  blocked?: boolean;
}) {
  const ups = dependsOn ?? [];
  const downs = blocking ?? [];
  if (!blocked && ups.length === 0 && downs.length === 0) return null;
  const unmet = blockedBy ?? [];
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {blocked && (
        <span
          className="inline-flex items-center gap-1 rounded-full bg-orange-500/10 px-2 py-0.5 font-mono text-[10px] font-medium text-orange-300 ring-1 ring-inset ring-orange-500/40"
          title={
            unmet.length > 0
              ? `Waiting on tasks: ${unmet.map((n) => `#${n}`).join(", ")}`
              : "Waiting on dependencies"
          }
        >
          ⛔ blocked
        </span>
      )}
      {ups.map((n) => (
        <Link
          key={`dep-${n}`}
          to={`/tasks/${n}`}
          onClick={(e) => e.stopPropagation()}
          className="rounded-full bg-ink-800/70 px-2 py-0.5 font-mono text-[10px] text-ink-300 ring-1 ring-inset ring-ink-700/60 transition-colors hover:text-syrup-300 hover:ring-syrup-500/40"
          title={`This task depends on #${n}`}
        >
          depends on #{n}
        </Link>
      ))}
      {downs.map((n) => (
        <Link
          key={`blk-${n}`}
          to={`/tasks/${n}`}
          onClick={(e) => e.stopPropagation()}
          className="rounded-full bg-ink-800/70 px-2 py-0.5 font-mono text-[10px] text-ink-300 ring-1 ring-inset ring-ink-700/60 transition-colors hover:text-syrup-300 hover:ring-syrup-500/40"
          title={`Task #${n} depends on this task`}
        >
          blocks #{n}
        </Link>
      ))}
    </span>
  );
}
