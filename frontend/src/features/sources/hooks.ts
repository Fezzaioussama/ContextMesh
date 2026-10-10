import { useCallback, useEffect, useRef, useState } from "react";
import { describeError, sourceDocuments, sources } from "../../api/client";
import type { DocumentSummary, Source } from "../../api/contracts";
import { isPending } from "./documentStatus";

const POLL_MS = 1500;

export function useSources() {
  const [items, setItems] = useState<Source[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setError("");
    async function load() {
      try {
        const result = await sources(controller.signal);
        if (!controller.signal.aborted) setItems(result);
      } catch (failure) {
        if (!controller.signal.aborted) setError(describeError(failure));
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [revision]);

  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  const searchable = items.filter((item) => item.searchable_count > 0);
  return { items, searchable, loading, error, refresh };
}

export type SourcesState = ReturnType<typeof useSources>;

/** A source's documents, polled while any is still indexing; `onSettled` runs when they finish. */
export function useDocuments(sourceId: string, onSettled: () => void) {
  const [items, setItems] = useState<DocumentSummary[]>([]);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    setError("");
    async function load() {
      try {
        const result = await sourceDocuments(sourceId, controller.signal);
        if (!controller.signal.aborted) setItems(result);
      } catch (failure) {
        if (!controller.signal.aborted) setError(describeError(failure));
      }
    }
    void load();
    return () => controller.abort();
  }, [sourceId, revision]);

  const pending = items.some(isPending);
  usePolling(pending, revision, refresh);
  const wasPending = useRef(pending);
  useEffect(() => {
    if (wasPending.current && !pending) onSettled();
    wasPending.current = pending;
  }, [pending, onSettled]);
  return { items, error, pending, refresh };
}

/** Calls `refresh` once per change of `key` while `active`; stops when inactive. */
export function usePolling(active: boolean, key: unknown, refresh: () => void) {
  useEffect(() => {
    if (!active) return;
    const timer = window.setTimeout(refresh, POLL_MS);
    return () => window.clearTimeout(timer);
  }, [active, key, refresh]);
}

/** Runs one user-initiated mutation at a time and reports a safe error. */
export function useAction<Args extends unknown[]>(
  action: (...args: Args) => Promise<unknown>,
  done: () => void,
) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  async function run(...args: Args) {
    setPending(true);
    setError("");
    try {
      await action(...args);
      done();
    } catch (failure) {
      setError(describeError(failure));
    } finally {
      setPending(false);
    }
  }

  return { pending, error, run, clear: () => setError("") };
}
