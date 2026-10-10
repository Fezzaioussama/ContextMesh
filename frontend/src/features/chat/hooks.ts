import { useCallback, useEffect, useRef, useState } from "react";
import {
  conversations,
  createConversation,
  describeError,
  history,
  metadata,
  sendMessage,
} from "../../api/client";
import type {
  AgentMetadata,
  CompletedTurn,
  Conversation,
  Message,
} from "../../api/contracts";

export const AGENT_NAME = "ContextMesh Agent";

// Browser storage may be unavailable (private mode, policies); the server keeps history.

const selectionKey = "contextmesh.selected-conversation";
const attemptKey = (id: string) => `contextmesh.turn.${id}`;

function safely<T>(action: () => T, fallback: T): T {
  try {
    return action();
  } catch {
    return fallback;
  }
}

export function readSelection(): string | null {
  return safely(() => localStorage.getItem(selectionKey), null);
}

export function saveSelection(id: string): void {
  safely(() => localStorage.setItem(selectionKey, id), undefined);
}

/** An unsent or failed message, kept with its idempotency key so a retry is not duplicated. */
interface PendingAttempt {
  message: string;
  sourceIds: string[] | null;
  key: string;
}

function readAttempt(id: string): PendingAttempt | null {
  return safely(() => {
    const stored = sessionStorage.getItem(attemptKey(id));
    if (stored === null) return null;
    const attempt = JSON.parse(stored) as PendingAttempt;
    return { ...attempt, sourceIds: attempt.sourceIds ?? null };
  }, null);
}

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

export function useConversations() {
  const [items, setItems] = useState<Conversation[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const active = useRef<AbortController | null>(null);

  /** Loads the first page (replacing the list) or the next one (appending to it). */
  const load = useCallback(async (next: string | null, append: boolean) => {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setLoading(true);
    setError("");
    try {
      const page = await conversations(next, controller.signal);
      if (controller.signal.aborted) return;
      setItems((previous) => {
        if (!append) return page.items;
        const unique = new Map(previous.map((item) => [item.id, item]));
        page.items.forEach((item) => unique.set(item.id, item));
        return [...unique.values()];
      });
      setCursor(page.next_cursor);
    } catch (failure) {
      if (!controller.signal.aborted) setError(describeError(failure));
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(null, false);
    return () => active.current?.abort();
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

export function useCreateConversation(
  created: (conversation: Conversation, draft: string) => void,
) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const active = useRef<AbortController | null>(null);

  useEffect(() => () => active.current?.abort(), []);

  async function create(draft: string) {
    if (active.current !== null) return;
    const controller = new AbortController();
    active.current = controller;
    setPending(true);
    setError("");
    try {
      const title = draft.length === 0 ? "New conversation" : draft.slice(0, 100);
      const result = await createConversation(title, controller.signal);
      if (!controller.signal.aborted) created(result, draft);
    } catch (failure) {
      if (!controller.signal.aborted) setError(describeError(failure));
    } finally {
      if (!controller.signal.aborted) setPending(false);
      active.current = null;
    }
  }

  return { pending, error, create };
}

export function useHistory(conversationId: string) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    async function load() {
      try {
        const result = await history(conversationId, controller.signal);
        if (!controller.signal.aborted) setMessages(result);
      } catch (failure) {
        if (!controller.signal.aborted) setError(describeError(failure));
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [conversationId, revision]);

  /** Add a finished turn's messages without reloading the conversation. */
  function complete(turn: CompletedTurn) {
    setMessages((previous) => {
      const merged = new Map(previous.map((message) => [message.id, message]));
      merged.set(turn.user_message.id, turn.user_message);
      merged.set(turn.assistant_message.id, turn.assistant_message);
      return [...merged.values()];
    });
  }

  return {
    messages,
    loading,
    error,
    complete,
    refresh: () => setRevision((value) => value + 1),
  };
}

/**
 * The composer's draft and its submission. A failed message keeps its idempotency
 * key, so retrying the same message and scope cannot create a second turn.
 */
export function useTurn(
  id: string,
  suggestion: string,
  scope: string[] | null,
  onComplete: (turn: CompletedTurn) => void,
) {
  const [draft, setDraft] = useState(() => readAttempt(id)?.message ?? suggestion);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const active = useRef<AbortController | null>(null);
  const attempt = useRef(readAttempt(id));

  useEffect(() => () => active.current?.abort(), []);

  async function submit() {
    if (active.current !== null) return;
    const message = draft.trim();
    if (message.length === 0) return;
    const controller = new AbortController();
    active.current = controller;
    const previous = attempt.current;
    const unchanged =
      previous !== null &&
      previous.message === message &&
      JSON.stringify(previous.sourceIds) === JSON.stringify(scope);
    const current = unchanged
      ? previous
      : { message, sourceIds: scope, key: crypto.randomUUID() };
    attempt.current = current;
    safely(() => sessionStorage.setItem(attemptKey(id), JSON.stringify(current)), undefined);
    setPending(true);
    setError("");
    try {
      const turn = await sendMessage(
        id,
        { message: current.message, sourceIds: current.sourceIds },
        current.key,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      safely(() => sessionStorage.removeItem(attemptKey(id)), undefined);
      attempt.current = null;
      setDraft("");
      onComplete(turn);
    } catch (failure) {
      if (!controller.signal.aborted) setError(describeError(failure));
    } finally {
      if (!controller.signal.aborted) setPending(false);
      active.current = null;
    }
  }

  return { draft, setDraft, pending, error, submit };
}
