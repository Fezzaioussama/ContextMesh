import { useEffect, useRef, useState } from "react";
import { createConversation, describeError } from "../../api/client";
import type { Conversation } from "../../api/contracts";

export function useCreateConversation(
  created: (conversation: Conversation, draft: string) => void,
) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const active = useRef<AbortController | null>(null);

  useEffect(
    () => () => {
      if (active.current !== null) active.current.abort();
    },
    [],
  );

  async function create(draft: string) {
    if (active.current !== null) return;
    const controller = new AbortController();
    active.current = controller;
    setPending(true);
    setError("");
    await execute(draft, controller);
  }

  async function execute(draft: string, controller: AbortController) {
    try {
      const title = conversationTitle(draft);
      const result = await createConversation(title, controller.signal);
      if (!controller.signal.aborted) created(result, draft);
    } catch (failure) {
      if (!controller.signal.aborted) setError(describeError(failure));
    } finally {
      finish(controller.signal);
      active.current = null;
    }
  }

  function finish(signal: AbortSignal) {
    if (!signal.aborted) setPending(false);
  }

  return { pending, error, create };
}

function conversationTitle(draft: string) {
  if (draft.length === 0) return "New conversation";
  return draft.slice(0, 100);
}
