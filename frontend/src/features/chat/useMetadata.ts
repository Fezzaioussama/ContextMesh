import { useEffect, useState } from "react";
import { describeError, metadata } from "../../api/client";
import type { AgentMetadata } from "../../api/contracts";

export function useMetadata() {
  const [agent, setAgent] = useState<AgentMetadata | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setError("");
    async function load() {
      try {
        const result = await metadata(controller.signal);
        if (!controller.signal.aborted) setAgent(result);
      } catch (failure) {
        if (!controller.signal.aborted) setError(describeError(failure));
      }
    }
    void load();
    return () => controller.abort();
  }, [revision]);

  return { agent, error, refresh: () => setRevision((value) => value + 1) };
}
