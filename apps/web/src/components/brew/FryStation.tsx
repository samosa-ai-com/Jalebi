/**
 * Halwai Shop — one karhai (cooking pot) station on a gas stove (pure
 * geometric SVG, no assets).
 *
 * Heat levels: 0 = idle pilot (small blue flame + faint steam),
 * 1 = queued (medium blue + licks of orange), 2 = frying (full orange
 * flames), 3 = needs-you flare (taller, faster flames + pulse ring).
 * The snack matches the task type: freeform → jalebi coil (self-drawing),
 * issue_fix → samosa triangle (bobbing + browning), pr_review → pakora
 * fritters. Fry color deepens with progress. Idle stations offer a "strike a match" order button.
 */
import { useEffect, useState } from "react";
import { taskEvents } from "../../api/client";
import { buildActivityBars, lastMessageText } from "../../lib/runningCard";
import { Sparkline } from "../RunningCard";
import type { Step, Task } from "../../types";
import { snackForType, type SnackKind } from "./snacks";

export interface StationTask {
  task: Task;
  agentName: string | null;
  avatarUrl: string | null;
  /** Ingredient (skill) names the frying cook is using. */
  skillNames: string[];
  /** Run start as epoch ms (null when unknown); the station derives elapsed from `now`. */
  startedAtMs: number | null;
  timeoutMs: number | null;
  steps: number;
  repoName?: string | null;
}

function formatElapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  if (h > 0) return `${h}h ${m % 60}m`;
  if (m > 0) return `${m}m ${s % 60}s`;
  return `${s}s`;
}

/** Fry color: pale dough → gold → deep amber by progress. */
function fryColor(progress: number): string {
  if (progress < 0.33) return "#f6e3b4";
  if (progress < 0.7) return "#f7b955";
  return "#c2571b";
}

/** Teardrop flame jet centered at (x, base) with height h. */
function FlameJet({
  x,
  base,
  h,
  delay,
  fast,
}: {
  x: number;
  base: number;
  h: number;
  delay: string;
  fast?: boolean;
}) {
  return (
    <g
      className="brew-flame"
      style={{ animationDelay: delay, animationDuration: fast ? "0.55s" : "0.9s" }}
    >
      <path
        d={`M${x} ${base} q -5 ${-h * 0.5} 0 ${-h} q 5 ${h * 0.5} 0 ${h} Z`}
        fill="#f97316"
        opacity="0.9"
      />
      <path
        d={`M${x} ${base} q -2.5 ${-h * 0.3} 0 ${-h * 0.55} q 2.5 ${h * 0.3} 0 ${h * 0.55} Z`}
        fill="#38bdf8"
        opacity="0.85"
      />
    </g>
  );
}

function Snack({ kind, progress }: { kind: SnackKind; progress: number }) {
  const color = fryColor(progress);
  if (kind === "samosa") {
    // House logo, bobbing in the oil.
    return (
      <g className="brew-snack">
        <image
          href="/samosa.png"
          x="36"
          y="44"
          width="48"
          height="38"
          preserveAspectRatio="xMidYMid meet"
        />
      </g>
    );
  }
  if (kind === "pakora") {
    // A cluster of golden fritters, bobbing in the oil.
    return (
      <g className="brew-snack">
        <circle cx="52" cy="68" r="8" fill={color} stroke="#7c2d12" strokeWidth="1.5" />
        <circle cx="66" cy="66" r="9" fill={color} stroke="#7c2d12" strokeWidth="1.5" />
        <circle cx="59" cy="58" r="7" fill={color} stroke="#7c2d12" strokeWidth="1.5" />
        <g fill="#7c2d12" opacity="0.55">
          <circle cx="50" cy="66" r="1.2" />
          <circle cx="64" cy="63" r="1.2" />
          <circle cx="69" cy="70" r="1" />
          <circle cx="58" cy="56" r="1" />
        </g>
      </g>
    );
  }
  // jalebi coil: Archimedean-ish spiral that draws itself with progress.
  const coil =
    "M60 66 m0 0 c 6 0 9 3 9 7 c 0 5 -5 8 -10 7 c -6 -1 -9 -6 -7 -11 c 2 -6 8 -9 13 -7 c 5 2 7 7 5 12";
  const drawn = Math.max(0.08, progress);
  return (
    <g className="brew-snack">
      <path
        d={coil}
        fill="none"
        stroke={color}
        strokeWidth="4.5"
        strokeLinecap="round"
        pathLength={1}
        strokeDasharray="1"
        strokeDashoffset={1 - drawn}
        className="brew-coil-draw"
        style={{ filter: "drop-shadow(0 0 4px rgba(247,185,85,0.7))" }}
      />
    </g>
  );
}

export function FryStation({
  slot,
  station,
  now,
  compact = false,
  highlighted = false,
  onOpen,
  onOrder,
  onCancel,
}: {
  slot: number;
  station: StationTask | null;
  now: number;
  /** Smaller pots when the floor holds many stoves. */
  compact?: boolean;
  highlighted?: boolean;
  onOpen: (id: number) => void;
  onOrder: () => void;
  onCancel?: (taskId: number) => void;
}) {
  const brewing = station !== null;
  const needsYou = station?.task.attention === "needs_you";
  const queued = station?.task.status === "queued";
  const isRunning = station?.task.status === "running";
  const heat = !brewing ? 0 : needsYou ? 3 : queued ? 1 : 2;
  const elapsedMs =
    station?.startedAtMs === null || station?.startedAtMs === undefined
      ? 0
      : Math.max(0, now - (station?.startedAtMs ?? 0));
  const progress = station
    ? station.timeoutMs && station.timeoutMs > 0
      ? Math.min(1, elapsedMs / station.timeoutMs)
      : Math.min(1, 0.15 + station.steps * 0.05)
    : 0;
  const snack = snackForType(station?.task.type ?? "freeform");
  const clipId = `kadhai-oil-${slot}`;
  const jets = heat === 0 ? [52, 60, 68] : [44, 52, 60, 68, 76];
  const jetH = heat === 0 ? 7 : heat === 1 ? 11 : heat === 2 ? 16 : 20;

  const [liveMsg, setLiveMsg] = useState<string | null>(null);
  const [, setLiveTick] = useState(0);

  const taskId = station?.task.id;
  const runId = station?.task.run?.id;

  // Live SSE listener while frying: updates live step message and activity
  useEffect(() => {
    if (!isRunning || !taskId || !runId) return;
    const stop = taskEvents(
      taskId,
      (ev) => {
        if (ev.text) {
          const t = ev.text.replace(/\s+/g, " ").trim();
          if (t) setLiveMsg(t.length > 90 ? `${t.slice(0, 90)}…` : t);
        }
        setLiveTick((t) => t + 1);
      },
      () => setLiveTick((t) => t + 1)
    );
    return () => stop();
  }, [taskId, isRunning, runId]);

  const runSteps = (station?.task.run?.steps ?? []) as Step[];
  const lastMsg = (isRunning ? liveMsg : null) ?? lastMessageText(runSteps) ?? null;
  const bars = buildActivityBars(runSteps);
  const prNumber = station?.task.prs?.length
    ? station.task.prs[0]
    : (station?.task.pr_number ?? null);

  return (
    <div
      onClick={() => station && onOpen(station.task.id)}
      title={station ? `Open task #${station.task.id}` : `Stove ${slot}: pilot light on`}
      role={brewing ? "button" : undefined}
      tabIndex={brewing ? 0 : undefined}
      aria-label={
        station
          ? `Open task ${station.task.id} — ${needsYou ? "needs you" : queued ? "warming up" : "frying"}`
          : undefined
      }
      onKeyDown={(e) => {
        if (brewing && (e.key === "Enter" || e.key === " ")) {
          e.preventDefault();
          onOpen(station!.task.id);
        }
      }}
      className={`surface group w-full px-2.5 pt-1.5 pb-1.5 text-left transition-colors ${
        brewing ? "cursor-pointer hover:border-syrup-500/50" : ""
      } ${highlighted ? "ring-2 ring-syrup-500/60 border-syrup-500/80" : ""}`}
    >
      <svg
        viewBox="0 0 120 138"
        aria-hidden="true"
        preserveAspectRatio="xMidYMid meet"
        className={`mx-auto w-auto ${compact ? "h-36" : "h-48"}`}
      >
        {brewing && (
          <ellipse
            cx="60"
            cy="130"
            rx="36"
            ry="5"
            fill="#ef9b2f"
            opacity="0.22"
            className="brew-glow"
          />
        )}
        {/* steam */}
        {(heat === 0 || (brewing && !queued)) && (
          <g
            stroke="#d6c9ba"
            strokeWidth="2"
            strokeLinecap="round"
            fill="none"
            opacity={heat === 0 ? 0.35 : 0.7}
          >
            <path d="M50 50 q -6 -8 0 -16 q 6 -8 0 -16" className="brew-steam" />
            <path d="M68 50 q 6 -8 0 -16 q -6 -8 0 -16" className="brew-steam-late" />
          </g>
        )}
        {queued && (
          <text
            x="60"
            y="22"
            textAnchor="middle"
            fontSize="10"
            fill="#8d7a67"
            fontFamily="IBM Plex Mono, monospace"
          >
            warming up…
          </text>
        )}
        {/* kadhai bowl */}
        <path
          d="M22 62 L98 62 C98 92 80 106 60 106 C40 106 22 92 22 62 Z"
          fill={brewing ? "#241a10" : "#1a1410"}
          stroke={brewing ? "#ef9b2f" : "#33271e"}
          strokeWidth="2"
        />
        {/* oil + bubbles + snack, clipped to the bowl */}
        <clipPath id={clipId}>
          <path d="M22 62 L98 62 C98 92 80 106 60 106 C40 106 22 92 22 62 Z" />
        </clipPath>
        <g clipPath={`url(#${clipId})`}>
          <ellipse
            cx="60"
            cy="64"
            rx="38"
            ry="7"
            fill="#78350f"
            opacity={heat === 0 ? 0.35 : 0.7}
          />
          <g fill="#ffd97a">
            {(heat >= 2 ? [46, 54, 62, 70, 78, 58, 66] : [54, 66]).map((x, i) => (
              <circle
                key={i}
                cx={x}
                cy={86 - (i % 3) * 5}
                r={heat >= 2 ? 1.8 : 1.3}
                className="brew-bubble"
                style={{ animationDelay: `${-i * 0.35}s` }}
              />
            ))}
          </g>
          {brewing && <Snack kind={snack} progress={progress} />}
        </g>
        {/* oil surface line */}
        <ellipse
          cx="60"
          cy="64"
          rx="38"
          ry="7"
          fill="none"
          stroke="#f7b955"
          strokeWidth="1.5"
          opacity={brewing ? 0.9 : 0.35}
        />
        {/* stove grate + burner */}
        <line
          x1="34"
          y1="110"
          x2="86"
          y2="110"
          stroke="#4a3a2d"
          strokeWidth="3"
          strokeLinecap="round"
        />
        <rect x="42" y="110" width="36" height="7" rx="2" fill="#221a14" stroke="#4a3a2d" />
        {/* flames */}
        {jets.map((x, i) => (
          <FlameJet key={x} x={x} base={110} h={jetH} delay={`${-i * 0.18}s`} fast={heat >= 2} />
        ))}
        {/* needs-you flare ring */}
        {needsYou && (
          <rect
            x="14"
            y="30"
            width="92"
            height="86"
            rx="10"
            fill="none"
            stroke="#f7b955"
            strokeWidth="2"
            strokeDasharray="6 4"
            className="brew-needs-you"
          />
        )}
      </svg>

      {station ? (
        <span className="mt-1 block">
          <span className="flex items-center gap-1.5">
            {station.avatarUrl && (
              <img src={station.avatarUrl} alt="" className="h-4 w-4 shrink-0 rounded-full" />
            )}
            <span className="font-mono text-xs text-syrup-400">#{station.task.id}</span>
            <span className="font-mono text-[10px] text-ink-500">{snack}</span>
            {station.repoName && (
              <span
                className="max-w-28 truncate font-mono text-[10px] text-ink-500"
                title={station.repoName}
              >
                {station.repoName.split("/")[1] ?? station.repoName}
              </span>
            )}
            <span
              className={`ml-auto rounded-full px-1.5 py-px font-mono text-[10px] ${
                needsYou
                  ? "brew-needs-you bg-syrup-500/20 text-syrup-300"
                  : queued
                    ? "bg-ink-800 text-ink-400"
                    : "bg-green-500/10 text-green-300"
              }`}
            >
              {needsYou ? "needs you" : queued ? "warming up" : "frying"}
            </span>
          </span>

          <span
            className="mt-1 block truncate text-[11px] font-medium text-ink-200"
            title={station.task.prompt}
          >
            {station.task.prompt}
          </span>

          <span className="mt-0.5 flex items-center justify-between gap-1 text-[10px] text-ink-400">
            <span className="truncate">
              {station.agentName ?? station.task.cli ?? station.task.status}
              {station.skillNames.length > 0 && (
                <span
                  className="font-mono text-[10px] text-ink-500 ml-1"
                  title={station.skillNames.join(", ")}
                >
                  +{station.skillNames.slice(0, 2).join(", ")}
                  {station.skillNames.length > 2 ? ` +${station.skillNames.length - 2}` : ""}
                </span>
              )}
            </span>
            <span className="shrink-0 font-mono text-[10px] tabular-nums text-ink-500">
              {formatElapsed(elapsedMs)}
              {station.steps > 0 ? ` · ${station.steps} steps` : ""}
            </span>
          </span>

          {lastMsg && (
            <span
              className="mt-1 flex items-center gap-1.5 rounded bg-ink-950/60 px-1.5 py-0.5 font-mono text-[10px] text-syrup-300/90 border border-ink-850/80"
              title={lastMsg}
            >
              <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-syrup-400 animate-pulse" />
              <span className="truncate">{lastMsg}</span>
            </span>
          )}

          <span className="mt-1.5 flex items-center justify-between gap-1 pt-1 border-t border-ink-800/40">
            <span className="flex items-center gap-1.5">
              {bars.length > 0 && <Sparkline bars={bars} />}
              {prNumber != null && station.repoName && (
                <a
                  href={`https://github.com/${station.repoName}/pull/${prNumber}`}
                  target="_blank"
                  rel="noreferrer"
                  onClick={(e) => e.stopPropagation()}
                  className="font-mono text-[10px] text-syrup-400 hover:text-syrup-300 hover:underline"
                  title={`Open PR #${prNumber}`}
                >
                  PR #{prNumber} ↗
                </a>
              )}
            </span>

            <span className="flex items-center gap-1">
              {needsYou && (
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onOpen(station.task.id);
                  }}
                  className="cursor-pointer rounded bg-syrup-500/20 px-1.5 py-0.5 font-mono text-[10px] text-syrup-300 hover:bg-syrup-500/30"
                >
                  respond →
                </button>
              )}
              {onCancel && (queued || isRunning) && (
                <button
                  type="button"
                  title="Cancel task"
                  onClick={(e) => {
                    e.stopPropagation();
                    onCancel(station.task.id);
                  }}
                  className="cursor-pointer rounded px-1.5 py-0.5 font-mono text-[10px] text-ink-500 ring-1 ring-ink-800 transition-colors hover:bg-red-500/10 hover:text-red-300 hover:ring-red-500/30"
                >
                  cancel
                </button>
              )}
            </span>
          </span>
        </span>
      ) : (
        <span className="mt-1 block text-center">
          <span className="block font-mono text-[10px] text-ink-600">stove {slot} · simmering</span>
          <button
            type="button"
            aria-label={`Strike a match on stove ${slot} — start a new task`}
            title="Strike a match — start a new task"
            onClick={(e) => {
              e.stopPropagation();
              onOrder();
            }}
            className="mt-1 cursor-pointer rounded-full border border-ink-700 px-2.5 py-0.5 font-mono text-[10px] text-syrup-300 transition-colors hover:border-syrup-500/60 hover:bg-syrup-500/10"
          >
            🪔 strike a match
          </button>
        </span>
      )}
    </div>
  );
}

export default FryStation;
