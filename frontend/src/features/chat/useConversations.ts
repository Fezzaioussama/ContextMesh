import { useCallback, useEffect, useRef, useState } from "react";
import { conversations, describeError } from "../../api/client";
import type { Conversation } from "../../api/contracts";

export function useConversations() {
  const [items, setItems] = useState<Conversation[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const active = useRef<AbortController | null>(null);

  const load = useCallback(async (next: string | null, append: boolean) => {
    cancelActive();
    const controller = new AbortController();
    active.current = controller;
    setLoading(true);
    setError("");
    try {
      const page = await conversations(next, controller.signal);
      applyPage(page.items, page.next_cursor, append, controller.signal);
    } catch (failure) {
      if (!controller.signal.aborted) setError(describeError(failure));
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, []);

  function cancelActive() {
    if (active.current !== null) active.current.abort();
  }

  function applyPage(
    newItems: Conversation[],
    next: string | null,
    append: boolean,
    signal: AbortSignal,
  ) {
    if (signal.aborted) return;
    setItems((previous) => combineConversations(previous, newItems, append));
    setCursor(next);
  }

  useEffect(() => {
    void load(null, false);
    return () => {
      if (active.current !== null) active.current.abort();
    };
  }, [load]);

  function insert(conversation: Conversation) {
    setItems((previous) => [
      conversation,
      ...previous.filter((item) => item.id !== conversation.id),
    ]);
  }

  return {
    items,
    cursor,
    loading,
    error,
    insert,
    refresh: () => load(null, false),
    more: () => load(cursor, true),
  };
}

function combineConversations(
  previous: Conversation[],
  incoming: Conversation[],
  append: boolean,
) {
  if (!append) return incoming;
  const unique = new Map(previous.map((item) => [item.id, item]));
  incoming.forEach((item) => unique.set(item.id, item));
  return [...unique.values()];
}
