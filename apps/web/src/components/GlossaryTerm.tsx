import { useId, type ReactNode } from "react";
import { GLOSSARY } from "../lib/glossary";

/** Inline jargon hint: dotted underline + native tooltip, keyboard-focusable
 * with the definition exposed via aria-describedby. Unknown terms render plain. */
export default function GlossaryTerm({ term, children }: { term: string; children: ReactNode }) {
  const definition = GLOSSARY[term];
  const hintId = useId();
  if (!definition) return <>{children}</>;
  return (
    // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- intentional focusable hint
    <span tabIndex={0}
      aria-describedby={hintId}
      className="underline decoration-dotted underline-offset-2"
      title={definition}
    >
      {children}
      <span id={hintId} className="sr-only">
        {definition}
      </span>
    </span>
  );
}
