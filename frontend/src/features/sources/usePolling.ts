import { useEffect } from "react";

const POLL_MS = 1500;

/** Calls `refresh` once per change of `key` while `active`; stops when inactive. */
export function usePolling(active: boolean, key: unknown, refresh: () => void) {
  useEffect(() => {
    if (!active) return;
    const timer = window.setTimeout(refresh, POLL_MS);
    return () => window.clearTimeout(timer);
  }, [active, key, refresh]);
}
