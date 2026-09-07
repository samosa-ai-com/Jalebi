/**
 * Brew House — one worker-station kettle (pure geometric SVG, no assets).
 *
 * A station is either idle (cold, dim kettle) or brewing (a queued/running
 * task: amber glow, rising steam, liquid fill = elapsed/timeout). The whole
 * card is a button that opens the task.
 */
import type { Task } from "../../types";

export interface StationTask {
  task: Task;
  agentName: string | null;
  avatarUrl: string | null;
  /** Run start as epoch ms (null when unknown); the station derives elapsed from `now`. */
  startedAtMs: number | null;
  timeoutMs: number | null;
  steps: number;
}

function formatElapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  if (h > 0) return `${h}h ${m % 60}m`;
  if (m > 0) return `${m}m ${s % 60}s`;
  return `${s}s`;
}

export function KettleStation({
  slot,
  station,
  now,
  onOpen,
}: {
  slot: number;
  station: StationTask | null;
  now: number;
  onOpen: (id: number) => void;
}) {
  const brewing = station !== null;
  const needsYou = station?.task.attention === "needs_you";
  const queued = station?.task.status === "queued";
  const elapsedMs =
    station?.startedAtMs === null || station?.startedAtMs === undefined
      ? 0
      : Math.max(0, now - (station?.startedAtMs ?? 0));
  const fill = station
    ? station.timeoutMs && station.timeoutMs > 0
      ? Math.min(1, elapsedMs / station.timeoutMs)
      : 0.15
    : 0;
  // Liquid surface inside the kettle body (body spans y 62→118, x 28→92).
  const liquidTop = 118 - fill * 52;
  const clipId = `kettle-clip-${slot}`;

  return (
    <button
      type="button"
      disabled={!brewing}
      onClick={() => station && onOpen(station.task.id)}
      title={station ? `Open task #${station.task.id}` : `Worker slot ${slot}: idle`}
      aria-label={station ? `Open task ${station.task.id}` : `Worker slot ${slot} idle`}
      className={`surface group w-44 shrink-0 px-3 pt-3 pb-2 text-left transition-colors ${
        brewing ? "cursor-pointer hover:border-syrup-500/50" : "opacity-70"
      }`}
    >
      <svg viewBox="0 0 120 132" role="img" aria-hidden="true" className="w-full">
        {brewing && (
          <ellipse
            cx="60"
            cy="122"
            rx="34"
            ry="6"
            fill="#ef9b2f"
            opacity="0.25"
            className="brew-glow"
          />
        )}
        {/* steam */}
        {brewing && !queued && (
          <g stroke="#f7b955" strokeWidth="2.5" strokeLinecap="round" fill="none" opacity="0.8">
            <path d="M50 44 q -6 -8 0 -16 q 6 -8 0 -16" className="brew-steam" />
            <path d="M62 44 q 6 -8 0 -16 q -6 -8 0 -16" className="brew-steam-late" />
            <path d="M72 46 q -5 -7 0 -14 q 5 -7 0 -14" className="brew-steam" />
          </g>
        )}
        {brewing && queued && (
          <text
            x="60"
            y="26"
            textAnchor="middle"
            fontSize="11"
            fill="#8d7a67"
            fontFamily="IBM Plex Mono, monospace"
          >
            queued…
          </text>
        )}
        {/* handle */}
        <path
          d="M88 70 q 18 2 14 22 q -3 16 -20 16"
          fill="none"
          stroke={brewing ? "#c69b6b" : "#4a3a2d"}
          strokeWidth="5"
          strokeLinecap="round"
        />
        {/* spout */}
        <path d="M32 78 L14 62 L20 56 L38 70 Z" fill={brewing ? "#c69b6b" : "#4a3a2d"} />
        {/* body */}
        <path
          d="M28 62 L92 62 L86 118 L34 118 Z"
          fill={brewing ? "#2a2018" : "#1a1410"}
          stroke={brewing ? "#ef9b2f" : "#33271e"}
          strokeWidth="2"
        />
        {/* liquid fill, clipped to the kettle body so shallow-fill
            bubbles never render below the baseline */}
        {brewing && (
          <g>
            <clipPath id={clipId}>
              <path d="M28 62 L92 62 L86 118 L34 118 Z" />
            </clipPath>
            <g clipPath={`url(#${clipId})`}>
              <path
                d={`M30 ${liquidTop + 2} L90 ${liquidTop + 2} L86 116 L34 116 Z`}
                fill="#ef9b2f"
                opacity="0.35"
              />
              {!queued && (
                <g fill="#ffd97a">
                  <circle cx="48" cy={liquidTop + 16} r="2.2" className="brew-bubble" />
                  <circle
                    cx="62"
                    cy={liquidTop + 24}
                    r="1.7"
                    className="brew-bubble"
                    style={{ animationDelay: "-0.7s" }}
                  />
                  <circle
                    cx="74"
                    cy={liquidTop + 14}
                    r="2"
                    className="brew-bubble"
                    style={{ animationDelay: "-1.1s" }}
                  />
                </g>
              )}
            </g>
            <line
              x1="30"
              y1={liquidTop + 2}
              x2="90"
              y2={liquidTop + 2}
              stroke="#f7b955"
              strokeWidth="2"
            />
          </g>
        )}
        {/* lid + knob */}
        <rect
          x="40"
          y="52"
          width="40"
          height="10"
          rx="3"
          fill={brewing ? "#b08050" : "#33271e"}
          stroke={brewing ? "#ef9b2f" : "#4a3a2d"}
        />
        <circle cx="60" cy="50" r="4" fill={brewing ? "#f7b955" : "#4a3a2d"} />
        {/* needs-you ring */}
        {needsYou && (
          <rect
            x="22"
            y="44"
            width="76"
            height="82"
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
              <img src={station.avatarUrl} alt="" className="h-4 w-4 rounded-full" />
            )}
            <span className="font-mono text-xs text-syrup-400">#{station.task.id}</span>
            {needsYou && <span className="h-1.5 w-1.5 rounded-full bg-syrup-400 brew-needs-you" />}
          </span>
          <span
            className="mt-0.5 block truncate text-[11px] text-ink-300"
            title={station.task.prompt}
          >
            {station.agentName ?? station.task.cli ?? station.task.status}
          </span>
          <span className="font-mono text-[10px] tabular-nums text-ink-500">
            {formatElapsed(elapsedMs)}
            {station.steps > 0 ? ` · ${station.steps} steps` : ""}
          </span>
        </span>
      ) : (
        <span className="mt-1 block text-center font-mono text-[10px] text-ink-600">
          slot {slot} · idle
        </span>
      )}
    </button>
  );
}

export default KettleStation;
