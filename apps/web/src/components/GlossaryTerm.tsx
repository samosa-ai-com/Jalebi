import type { ReactNode } from "react";
import { GLOSSARY } from "../lib/glossary";

/** Inline jargon hint: dotted underline + native tooltip. Unknown terms render plain. */
export default function GlossaryTerm({ term, children }: { term: string; children: ReactNode }) {
  const definition = GLOSSARY[term];
  if (!definition) return <>{children}</>;
  return (
    <span className="underline decoration-dotted underline-offset-2" title={definition}>
      {children}
    </span>
  );
}
