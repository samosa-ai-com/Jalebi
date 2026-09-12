import { useState } from "react";
import { Link } from "react-router-dom";

export const ONBOARDING_DISMISSED_KEY = "jalebi-onboarding-dismissed";

export interface OnboardingChecklistProps {
  accounts: number;
  repos: number;
  tasks: number;
  onStartTask?: () => void;
}

export function OnboardingChecklist({
  accounts,
  repos,
  tasks,
  onStartTask,
}: OnboardingChecklistProps) {
  const [dismissed, setDismissed] = useState(() => {
    try {
      return localStorage.getItem(ONBOARDING_DISMISSED_KEY) === "1";
    } catch {
      return false;
    }
  });

  const step1Done = accounts > 0;
  const step2Done = repos > 0;
  const step3Done = tasks > 0;

  if (dismissed || (step1Done && step2Done && step3Done)) {
    return null;
  }

  const doneCount = (step1Done ? 1 : 0) + (step2Done ? 1 : 0) + (step3Done ? 1 : 0);

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
      done: step1Done,
      action: (
        <Link
          to="/github"
          className={step1Done ? "text-ink-500 line-through" : "link"}
        >
          Add your GitHub account
        </Link>
      ),
    },
    {
      id: "repo",
      label: "Connect a repository",
      done: step2Done,
      action: (
        <Link
          to="/repos"
          className={step2Done ? "text-ink-500 line-through" : "link"}
        >
          Connect a repository
        </Link>
      ),
    },
    {
      id: "task",
      label: "Create your first task",
      done: step3Done,
      action: (
        <button
          type="button"
          onClick={onStartTask}
          className={step3Done ? "text-ink-500 line-through text-left" : "link text-left"}
        >
          Create your first task
        </button>
      ),
    },
  ];

  return (
    <div className="surface p-4 animate-fade-up">
      <div className="flex items-center justify-between pb-3 border-b border-ink-800">
        <div className="flex items-center gap-2">
          <h2 className="panel-title">Setup</h2>
          <span className="text-xs text-ink-500 font-mono">
            {doneCount} of 3 done
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
        {steps.map((step, idx) => (
          <li
            key={step.id}
            data-testid={`step-${step.id}`}
            data-done={step.done ? "true" : "false"}
            className={`flex items-center gap-2.5 text-sm ${
              step.done ? "text-ink-500" : "text-ink-200"
            }`}
          >
            <span
              aria-hidden="true"
              className={`inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold ${
                step.done
                  ? "bg-green-500/20 text-green-400 font-bold"
                  : "border border-ink-700 text-ink-500"
              }`}
            >
              {step.done ? "✓" : idx + 1}
            </span>
            {step.action}
          </li>
        ))}
      </ul>
    </div>
  );
}

export default OnboardingChecklist;
