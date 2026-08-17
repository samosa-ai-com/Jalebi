/**
 * Hand-rolled IntersectionObserver hook (Phase 4 T3.3).
 *
 * Lazy-mounts a section's content when the section header is within ~300 px
 * of the viewport. Falls back to "always in view" when IntersectionObserver
 * is unavailable (vitest + jsdom), so tests are deterministic and SSR is
 * safe.
 */
import { useEffect, useRef, useState } from "react";

export function useInView<T extends Element = Element>(): {
  ref: React.RefObject<T | null>;
  inView: boolean;
} {
  const ref = useRef<T | null>(null);
  const [inView, setInView] = useState<boolean>(
    typeof IntersectionObserver === "undefined"
  );

  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const el = ref.current;
    if (!el) return;
    let cancelled = false;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (cancelled) return;
          if (entry.isIntersecting) {
            setInView(true);
            // Once visible, keep it visible — never collapse after scroll.
            observer.disconnect();
            return;
          }
        }
      },
      { rootMargin: "300px 0px", threshold: 0 }
    );
    observer.observe(el);
    return () => {
      cancelled = true;
      observer.disconnect();
    };
  }, []);

  return { ref, inView };
}