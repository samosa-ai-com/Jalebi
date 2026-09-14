import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

export interface SearchableOption {
  value: string;
  label?: string;
}

interface SearchableSelectProps {
  label: string;
  value: string | number;
  onChange: (v: string) => void;
  options: Array<string | SearchableOption>;
  placeholder?: string;
  disabled?: boolean;
  /** When true, the search text can be submitted as a custom value
   * (model inputs for backends without a catalog). */
  allowCustom?: boolean;
  /** Hint shown when the current value is not among the options. */
  staleHint?: string;
  /** Hide the visible label (the label text stays the accessible name). */
  hideLabel?: boolean;
  /** Optional native tooltip on the visible label (jargon hint; aria-label untouched). */
  labelTitle?: string;
}

function norm(o: string | SearchableOption): { value: string; label: string } {
  return typeof o === "string"
    ? { value: o, label: o }
    : { value: o.value, label: o.label ?? o.value };
}

/** Searchable dropdown replacing native `<select>` app-wide. Filters options
 * by substring (case-insensitive); keyboard navigable (Up/Down/Enter/Escape);
 * closes on outside click. Never clears the controlled value itself — a value
 * missing from the options stays visible with a warning, so saved pins and
 * custom entries survive async list loads; forms clear stale values
 * explicitly on backend change instead. */
export default function SearchableSelect({
  label,
  value,
  onChange,
  options,
  placeholder,
  disabled,
  allowCustom,
  staleHint = "Not in the known list — will be sent as-is.",
  hideLabel,
  labelTitle,
}: SearchableSelectProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [highlight, setHighlight] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  // Viewport-anchored coordinates for the portaled list (null = not placed yet).
  const [coords, setCoords] = useState<{ top: number; left: number; width: number } | null>(
    null
  );

  // The list lives in a document.body portal (above every card's stacking
  // context), so it is anchored to the toggle's viewport rect and flipped
  // upward when space below is tight. Recomputed on open + scroll + resize.
  function place() {
    const rect = buttonRef.current?.getBoundingClientRect();
    if (!rect) return;
    const GAP = 4;
    const MAX_H = 224; // matches max-h-56 on the list
    const MIN_W = 180; // matches the Math.max on the rendered width below
    const below = window.innerHeight - rect.bottom;
    const up = below < MAX_H + GAP && rect.top > below;
    const renderWidth = Math.max(rect.width, MIN_W);
    setCoords({
      left: Math.max(8, Math.min(rect.left, window.innerWidth - renderWidth - 8)),
      width: renderWidth,
      top: up ? Math.max(8, rect.top - MAX_H - GAP) : rect.bottom + GAP,
    });
  }

  useLayoutEffect(() => {
    if (open) place();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open]);
  const listId = useId();
  const current = String(value ?? "");

  const items = useMemo(() => options.map(norm), [options]);
  const selected = items.find((o) => o.value === current) ?? null;
  const stale = current !== "" && selected === null;

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter(
      (o) => o.label.toLowerCase().includes(q) || o.value.toLowerCase().includes(q)
    );
  }, [items, query]);

  const showCustom =
    allowCustom &&
    query.trim() !== "" &&
    !items.some(
      (o) =>
        o.value.toLowerCase() === query.trim().toLowerCase() ||
        o.label.toLowerCase() === query.trim().toLowerCase()
    );

  function openList() {
    if (disabled) return;
    setQuery("");
    setHighlight(0);
    setOpen(true);
    requestAnimationFrame(() => searchRef.current?.focus());
  }

  // Close on outside click (the portaled list is outside rootRef, so both
  // nodes count as inside — otherwise option mousedown would close first).
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      const target = e.target as Node;
      if (rootRef.current?.contains(target)) return;
      if (listRef.current?.contains(target)) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  function choose(v: string) {
    onChange(v);
    setOpen(false);
  }

  function onSearchKey(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      setOpen(false);
      return;
    }
    // The placeholder/clear row (index -1) is keyboard-reachable via ArrowUp
    // from the top; it only exists when no search text is typed.
    const canClear = placeholder !== undefined && query.trim() === "";
    const count = filtered.length + (showCustom ? 1 : 0);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => Math.min(h + 1, Math.max(count - 1, 0)));
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => (h <= 0 ? (canClear ? -1 : 0) : h - 1));
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      if (showCustom && highlight === filtered.length) {
        choose(query.trim());
      } else if (highlight === -1 && placeholder !== undefined && query.trim() === "") {
        choose("");
      } else if (filtered[highlight]) {
        choose(filtered[highlight].value);
      }
    }
  }

  return (
    <div ref={rootRef} className="block">
      {!hideLabel &&
        (labelTitle ? (
          <span
            className="mb-1.5 block w-fit text-xs font-medium text-ink-400 underline decoration-dotted underline-offset-2"
            title={labelTitle}
          >
            {label}
          </span>
        ) : (
          <span className="mb-1.5 block text-xs font-medium text-ink-400">{label}</span>
        ))}
      <button
        ref={buttonRef}
        type="button"
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => (open ? setOpen(false) : openList())}
        onKeyDown={(e) => {
          if (e.key === "Escape") setOpen(false);
        }}
        className="field flex items-center justify-between gap-2 text-left disabled:opacity-50"
      >
        <span className={selected || current ? "truncate" : "truncate text-ink-500"}>
          {selected ? selected.label : current || placeholder || "Select…"}
        </span>
        <span aria-hidden className="shrink-0 text-ink-500">
          {open ? "▴" : "▾"}
        </span>
      </button>
      {stale && (
        <p className="mt-1 text-[11px] leading-snug text-amber-300">
          {allowCustom ? `Custom value: ${current}.` : ""} {staleHint}
        </p>
      )}
      {open &&
        coords &&
        createPortal(
          <div
            ref={listRef}
            className="overflow-hidden rounded-lg border border-ink-700 bg-ink-950 shadow-xl"
            style={{
              position: "fixed",
              top: coords.top,
              left: coords.left,
              width: coords.width,
              zIndex: 50,
            }}
          >
            <input
              ref={searchRef}
              role="combobox"
              aria-label={`Search ${label}`}
              aria-expanded
              aria-controls={listId}
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setHighlight(0);
              }}
              onKeyDown={onSearchKey}
              placeholder={placeholder ? `Search ${placeholder.toLowerCase()}…` : "Search…"}
              className="w-full border-b border-ink-800 bg-transparent px-3 py-2 text-sm text-ink-100 outline-none placeholder:text-ink-500"
            />
            <ul role="listbox" id={listId} className="max-h-56 overflow-auto py-1">
              {placeholder !== undefined && query.trim() === "" && (
                <li
                  role="option"
                  aria-selected={current === ""}
                  onClick={() => choose("")}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      choose("");
                    }
                  }}
                  onMouseEnter={() => setHighlight(-1)}
                  className={`cursor-pointer px-3 py-1.5 text-sm ${
                    highlight === -1 ? "bg-ink-800" : ""
                  } ${current === "" ? "text-syrup-300" : "text-ink-500"}`}
                >
                  {placeholder}
                </li>
              )}
              {filtered.map((o, i) => (
                <li
                  key={o.value}
                  role="option"
                  aria-selected={o.value === current}
                  onClick={() => choose(o.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      choose(o.value);
                    }
                  }}
                  onMouseEnter={() => setHighlight(i)}
                  className={`cursor-pointer truncate px-3 py-1.5 font-mono text-[13px] ${
                    highlight === i ? "bg-ink-800" : ""
                  } ${o.value === current ? "text-syrup-300" : "text-ink-200"}`}
                >
                  {o.label}
                </li>
              ))}
              {showCustom && (
                <li
                  role="option"
                  aria-selected={false}
                  onClick={() => choose(query.trim())}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      choose(query.trim());
                    }
                  }}
                  onMouseEnter={() => setHighlight(filtered.length)}
                  className={`cursor-pointer px-3 py-1.5 text-sm ${
                    highlight === filtered.length ? "bg-ink-800" : ""
                  } text-chai-300`}
                >
                  Use custom value: <span className="font-mono">{query.trim()}</span>
                </li>
              )}
              {filtered.length === 0 && !showCustom && (
                <li className="px-3 py-1.5 text-sm text-ink-500">No matches.</li>
              )}
            </ul>
          </div>,
          document.body
        )}
    </div>
  );
}
