/**
 * Halwai Shop — pantry. Skills are the shop's ingredients: plain chips
 * with a colored dot, no art. An ingredient in a frying task glows and is
 * marked "in the karhai".
 */
import type { LibrarySkill } from "../../types";

export interface PantryIngredient {
  skill: LibrarySkill;
  /** How many cooks (agents) keep this ingredient. */
  uses: number;
  /** True while a frying task's cook uses this ingredient. */
  inPlay: boolean;
  /** Stove numbers (1-based) where this ingredient is currently in the karhai. */
  stoveSlots?: number[];
}

const DOT_COLORS = [
  "bg-syrup-400",
  "bg-chai-400",
  "bg-green-400",
  "bg-sky-400",
  "bg-red-400",
  "bg-ink-400",
];

function dotFor(id: string): string {
  let h = 0;
  for (let i = 0; i < id.length; i += 1) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return DOT_COLORS[h % DOT_COLORS.length];
}

export function Pantry({
  ingredients,
  onHoverStove,
}: {
  ingredients: PantryIngredient[];
  onHoverStove?: (slot: number | null) => void;
}) {
  return (
    <section
      className="surface flex min-h-0 flex-col overflow-hidden px-3 py-2.5"
      aria-label="Ingredients"
    >
      <h3 className="panel-title">Pantry (skills)</h3>
      <p className="mt-0.5 text-[11px] text-ink-500">skills in the pantry</p>
      {ingredients.length === 0 ? (
        <p className="mt-3 text-xs text-ink-500">No skills in the library yet.</p>
      ) : (
        <ul className="mt-2 min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-0.5">
          {ingredients.map(({ skill, uses, inPlay, stoveSlots }) => (
            <li
              key={skill.id}
              onMouseEnter={() => stoveSlots?.[0] && onHoverStove?.(stoveSlots[0])}
              onMouseLeave={() => onHoverStove?.(null)}
              className={`flex items-center gap-2 rounded-lg border px-2 py-1.5 transition-colors ${
                inPlay
                  ? "brew-ingredient-live border-syrup-500/50 bg-syrup-500/5 hover:border-syrup-500/80 cursor-pointer"
                  : "border-ink-800/60 hover:border-ink-700"
              }`}
            >
              <span className={`h-2 w-2 shrink-0 rounded-full ${dotFor(skill.id)}`} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs text-ink-200">{skill.name}</span>
                <span className="block truncate font-mono text-[10px] text-ink-500">
                  {uses} cook{uses === 1 ? "" : "s"}
                </span>
              </span>
              {inPlay && (
                <span className="shrink-0 rounded-full bg-syrup-500/15 px-1.5 py-0.5 font-mono text-[10px] text-syrup-300">
                  in the karhai
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default Pantry;
