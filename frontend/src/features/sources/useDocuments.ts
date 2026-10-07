import { useCallback, useEffect, useRef, useState } from "react";
import { describeError } from "../../api/client";
import type { DocumentSummary } from "../../api/contracts";
import { sourceDocuments } from "../../api/knowledge";
import { isPending } from "./documentStatus";
import { usePolling } from "./usePolling";

export function useDocuments(sourceId: string, onSettled: () => void) {
  const [items, setItems] = useState<DocumentSummary[]>([]);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const result = await sourceDocuments(sourceId, controller.signal);
        if (!controller.signal.aborted) setItems(result);
      } catch (failure) {
        if (!controller.signal.aborted) setError(describeError(failure));
      }
    }
    setError("");
    void load();
    return () => controller.abort();
  }, [sourceId, revision]);

  const pending = items.some(isPending);
  usePolling(pending, revision, refresh);
  useSettled(pending, onSettled);
  return { items, error, pending, refresh };
}

function useSettled(pending: boolean, onSettled: () => void) {
  const previous = useRef(pending);
  useEffect(() => {
    if (previous.current && !pending) onSettled();
    previous.current = pending;
  }, [pending, onSettled]);
}
