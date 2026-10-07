import { useCallback, useEffect, useState } from "react";
import { describeError } from "../../api/client";
import type { Source } from "../../api/contracts";
import { sources } from "../../api/knowledge";

export function useSources() {
  const [items, setItems] = useState<Source[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setError("");
    function show(result: Source[]) {
      if (!controller.signal.aborted) setItems(result);
    }
    async function load() {
      try {
        show(await sources(controller.signal));
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
