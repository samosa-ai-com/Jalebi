import { useState } from "react";
import { Link } from "react-router-dom";

// Versioned: the checklist grew 3 → 5 steps, so upgraders who dismissed the
// old card see the new steps once instead of staying dismissed forever.
export const ONBOARDING_DISMISSED_KEY = "jalebi-onboarding-dismissed-v2";
export const ONBOARDING_MANUAL_DONE_KEY = "jalebi-onboarding-manual-done-v1";

function loadManualDone(): Record<string, true> {
  try {
    const raw = localStorage.getItem(ONBOARDING_MANUAL_DONE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : {};
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return Object.fromEntries(
        Object.entries(parsed as Record<string, unknown>).filter(([, v]) => v === true)
      ) as Record<string, true>;
    }
  } catch {
    // Corrupt storage falls back to nothing manually completed.
  }
  return {};
}

export interface OnboardingChecklistProps {
  accounts: number;
  repos: number;
  tasks: number;
  hasModelChoice?: boolean;
  hasPublishedPr?: boolean;
  onStartTask?: () => void;
}

export function OnboardingChecklist({
  accounts,
  repos,
  tasks,
  hasModelChoice = false,
  hasPublishedPr = false,
  onStartTask,
}: OnboardingChecklistProps) {
  const [dismissed, setDismissed] = useState(() => {
    try {
      return localStorage.getItem(ONBOARDING_DISMISSED_KEY) === "1";
    } catch {
      return false;
    }
  });
  const [manualDone, setManualDone] = useState<Record<string, true>>(loadManualDone);

  function markStepDone(id: string) {
    setManualDone((prev) => {
      const next = { ...prev, [id]: true as const };
      try {
        localStorage.setItem(ONBOARDING_MANUAL_DONE_KEY, JSON.stringify(next));
      } catch {
        // Storage failures (private mode quota) just lose persistence.
      }
      return next;
    });
  }

  const autoDone: Record<string, boolean> = {
    account: accounts > 0,
    repo: repos > 0,
    task: tasks > 0,
    backend: hasModelChoice,
    pr: hasPublishedPr,
  };
  const isDone = (id: string) => autoDone[id] || manualDone[id] === true;

  if (dismissed || Object.keys(autoDone).every(isDone)) {
    return null;
  }

  const doneCount = Object.keys(autoDone).filter(isDone).length;

  const handleDismiss = () => {
    try {
      localStorage.setItem(ONBOARDING_DISMISSED_KEY, "1");
    } catch {
      // Storage unavailable or blocked
    }
    setDismissed(true);
  };

  const steps = [
    {
      id: "account",
      label: "Add your GitHub account",
      action: (
        <Link to="/github" className="link">
          Add your GitHub account
        </Link>
      ),
    },
    {
      id: "repo",
      label: "Connect a repository",
      action: (
        <Link to="/repos" className="link">
          Connect a repository
        </Link>
      ),
    },
    {
      id: "task",
      label: "Create your first task",
      action: (
        <button type="button" onClick={onStartTask} className="link text-left">
          Create your first task
        </button>
      ),
    },
    {
      id: "backend",
      label: "Choose your AI backend & model",
      action: (
        <Link to="/settings" className="link">
          Choose your AI backend & model
        </Link>
      ),
    },
    {
      id: "pr",
      label: "Publish your first PR",
      action: (
        <span className="text-ink-400">Publish a finished task to open a pull request</span>
      ),
    },
  ];

  return (
    <div className="surface p-4 animate-fade-up">
      <div className="flex items-center justify-between pb-3 border-b border-ink-800">
        <div className="flex items-center gap-2">
          <h2 className="panel-title">Setup</h2>
          <span className="text-xs text-ink-500 font-mono">
            {doneCount} of {steps.length} done
          </span>
        </div>
        <button
          type="button"
          onClick={handleDismiss}
          aria-label="Dismiss setup"
          className="inline-flex min-h-6 items-center text-xs text-ink-500 hover:text-ink-300 transition-colors"
        >
          Dismiss
        </button>
      </div>

      <ul className="mt-3 space-y-2">
        {steps.map((step, idx) => {
          const done = isDone(step.id);
          return (
            <li
              key={step.id}
              data-testid={`step-${step.id}`}
              data-done={done ? "true" : "false"}
              className={`flex items-center gap-2.5 text-sm ${
                done ? "text-ink-500" : "text-ink-200"
              }`}
            >
              <span
                aria-hidden="true"
                className={`inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold ${
                  done
                    ? "bg-green-500/20 text-green-400 font-bold"
                    : "border border-ink-700 text-ink-500"
                }`}
              >
                {done ? "✓" : idx + 1}
              </span>
              <span className={`min-w-0 flex-1 ${done ? "line-through" : ""}`}>
                {step.action}
              </span>
              {!done && (
                <button
                  type="button"
                  onClick={() => markStepDone(step.id)}
                  aria-label={`Skip ${step.label} (mark done)`}
                  title="Mark done (skip this step)"
                  className="inline-flex min-h-6 shrink-0 items-center text-[11px] text-ink-500 transition-colors hover:text-syrup-300"
                >
                  Skip
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export default OnboardingChecklist;
