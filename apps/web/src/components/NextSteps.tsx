import type { NextStep } from "../lib/nextSteps";

function scrollToTarget(id: string) {
  const el = document.getElementById(id);
  if (el && typeof el.scrollIntoView === "function") {
    el.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

/** "What next" guidance card for the task detail page. Null when nothing applies. */
export default function NextSteps({ steps }: { steps: NextStep[] }) {
  if (steps.length === 0) return null;
  return (
    <section aria-label="What next" className="surface p-5 animate-fade-up">
      <h2 className="panel-title">What next</h2>
      <ol className="mt-2 space-y-1.5">
        {steps.map((step, idx) => (
          <li key={`${step.label}-${idx}`} className="flex items-center gap-2.5 text-sm">
            <span
              aria-hidden="true"
              className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-ink-700 text-[11px] text-ink-500"
            >
              {idx + 1}
            </span>
            {step.href ? (
              <a
                href={step.href}
                target="_blank"
                rel="noreferrer"
                className="link min-h-6 inline-flex items-center"
              >
                {step.label} <span aria-hidden="true">↗</span>
                <span className="sr-only"> (opens in new tab)</span>
              </a>
            ) : (
              <button
                type="button"
                onClick={() => step.targetId && scrollToTarget(step.targetId)}
                className="link min-h-6 inline-flex items-center text-left"
              >
                {step.label}
              </button>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
