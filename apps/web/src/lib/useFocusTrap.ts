import { useEffect, useRef } from "react";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(", ");

export function getFocusableElements(container: HTMLElement): HTMLElement[] {
  const elements = Array.from(
    container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)
  );
  return elements.filter((el) => {
    if (el.hasAttribute("disabled") || el.getAttribute("aria-hidden") === "true") {
      return false;
    }
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") {
      return false;
    }
    return true;
  });
}

/**
 * Traps focus within a container element for accessible modal dialogs.
 *
 * Implements:
 * 1. Open-focus: sets initial focus to an autofocus element, the first focusable element,
 *    or the container itself.
 * 2. Tab & Shift+Tab containment: cycles focus within the container.
 * 3. Return-focus-to-opener: restores focus to the previously active element on close/unmount.
 */
export function useFocusTrap<T extends HTMLElement = HTMLDivElement>(
  active: boolean = true,
  externalRef?: React.RefObject<T | null>
): React.RefObject<T | null> {
  const internalRef = useRef<T | null>(null);
  const ref = externalRef ?? internalRef;
  const openerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!active) return;

    // Record the element that had focus when the trap activated
    openerRef.current = document.activeElement as HTMLElement | null;

    const container = ref.current;
    if (container) {
      const focusables = getFocusableElements(container);
      const autoFocusEl = container.querySelector<HTMLElement>("[autofocus]");
      if (autoFocusEl && focusables.includes(autoFocusEl)) {
        autoFocusEl.focus();
      } else if (focusables.length > 0) {
        focusables[0].focus();
      } else if (typeof container.focus === "function") {
        container.focus();
      }
    }

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key !== "Tab") return;

      const currentContainer = ref.current;
      if (!currentContainer) return;

      const focusables = getFocusableElements(currentContainer);
      if (focusables.length === 0) {
        e.preventDefault();
        currentContainer.focus();
        return;
      }

      const first = focusables[0];
      const last = focusables[focusables.length - 1];

      // If focus is outside the container, pull it in
      if (!currentContainer.contains(document.activeElement)) {
        e.preventDefault();
        if (e.shiftKey) {
          last.focus();
        } else {
          first.focus();
        }
        return;
      }

      if (e.shiftKey) {
        if (document.activeElement === first || document.activeElement === currentContainer) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }

    document.addEventListener("keydown", handleKeyDown);

    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      openerRef.current?.focus?.();
    };
  }, [active, ref]);

  return ref;
}
