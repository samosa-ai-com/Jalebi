import { useEffect, useState } from "react";
import { api } from "../api/client";

const FALLBACK = ["opencode", "codex", "claude", "pi", "kilo", "qwen", "cline", "goose"];

/** Backends the app may use (owner-configured in Settings → Agent defaults).
 * Falls back to the full registry list when the endpoint is unreachable. */
export function useBackends(): string[] {
  const [backends, setBackends] = useState<string[]>(FALLBACK);
  useEffect(() => {
    let cancelled = false;
    api
      .getBackends()
      .then((b) => {
        if (!cancelled && Array.isArray(b.enabled) && b.enabled.length > 0) {
          setBackends(b.enabled);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);
  return backends;
}
