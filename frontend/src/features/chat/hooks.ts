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
  TurnStreamEvent,
} from "../../api/contracts";

export const AGENT_NAME = "ContextMesh Agent";

// Browser storage may be unavailable (private mode, policies); the server keeps history.

const selectionKey = "contextmesh.selected-conversation";
const attemptKey = (id: string) => `contextmesh.turn.${id}`;
const scopeKey = (id: string) => `contextmesh.scope.${id}`;

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

/** The sources a conversation searches; null (nothing saved) means every source. */
export function readScope(id: string | null): string[] | null {
  if (id === null) return null;
  return safely(() => {
    const stored = localStorage.getItem(scopeKey(id));
    if (stored === null) return null;
    const value: unknown = JSON.parse(stored);
    const valid = Array.isArray(value) && value.every((item) => typeof item === "string");
    return valid ? (value as string[]) : null;
  }, null);
}

export function saveScope(id: string, scope: string[] | null): void {
  safely(() => {
    if (scope === null) localStorage.removeItem(scopeKey(id));
    else localStorage.setItem(scopeKey(id), JSON.stringify(scope));
  }, undefined);
}

/** An unsent or failed message, kept with its idempotency key so a retry is not duplicated. */
interface PendingAttempt {
  message: string;
  sourceIds: string[] | null;
  key: string;
}

/** The turn being answered: the sent message, the streamed draft text, and the latest progress label. */
export interface PendingTurn {
  message: string;
  text: string;
  progress: string;
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

/** `keepScope` carries the current conversation's sources into the new one. */
export function useCreateConversation(
  created: (conversation: Conversation, draft: string, keepScope: boolean) => void,
) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const active = useRef<AbortController | null>(null);
  const last = useRef({ draft: "", keepScope: false });

  useEffect(() => () => active.current?.abort(), []);

  async function create(draft: string, keepScope = false) {
    if (active.current !== null) return;
    last.current = { draft, keepScope };
    const controller = new AbortController();
    active.current = controller;
    setPending(true);
    setError("");
    try {
      const result = await createConversation(controller.signal);
      if (!controller.signal.aborted) created(result, draft, keepScope);
    } catch (failure) {
      if (!controller.signal.aborted) setError(describeError(failure));
    } finally {
      if (!controller.signal.aborted) setPending(false);
      active.current = null;
    }
  }

  const retry = () => create(last.current.draft, last.current.keepScope);
  return { pending, error, create, retry };
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
  const [stream, setStream] = useState<PendingTurn | null>(null);

  useEffect(() => {
    attempt.current = readAttempt(id);
    setDraft(attempt.current?.message ?? suggestion);
    setPending(false);
    setStream(null);
    setError("");
    return () => {
      active.current?.abort();
      active.current = null;
    };
  }, [id, suggestion]);

  function submit() {
    if (active.current !== null) return;
    const message = draft.trim();
    if (message.length === 0) return;
    const previous = attempt.current;
    const unchanged =
      previous !== null &&
      previous.message === message &&
      JSON.stringify(previous.sourceIds) === JSON.stringify(scope);
    const current = unchanged
      ? previous
      : { message, sourceIds: scope?.slice() ?? null, key: crypto.randomUUID() };
    void run(current);
  }

  async function run(current: PendingAttempt) {
    if (active.current !== null) return;
    const controller = new AbortController();
    active.current = controller;
    attempt.current = current;
    safely(() => sessionStorage.setItem(attemptKey(id), JSON.stringify(current)), undefined);
    setPending(true);
    setError("");
    setStream({ message: current.message, text: "", progress: "Preparing your answer…" });
    let text = "";
    function receive(event: TurnStreamEvent) {
      if (controller.signal.aborted) return;
      if (event.event === "delta") {
        text += event.data.text;
        if (text.length > 2_097_152) throw new Error("Answer exceeds stream limit");
        setStream((value) => value === null ? null : { ...value, text });
      }
      if (event.event === "progress" && event.data.stage !== "working")
        setStream((value) => value === null ? null : { ...value, progress: event.data.message });
    }
    try {
      const turn = await sendMessage(
        id,
        { message: current.message, sourceIds: current.sourceIds },
        current.key,
        controller.signal,
        receive,
      );
      if (controller.signal.aborted) return;
      safely(() => sessionStorage.removeItem(attemptKey(id)), undefined);
      attempt.current = null;
      setDraft("");
      onComplete(turn);
    } catch (failure) {
      if (!controller.signal.aborted) setError(describeError(failure));
    } finally {
      if (!controller.signal.aborted) {
        setPending(false);
        setStream(null);
      }
      if (active.current === controller) active.current = null;
    }
  }

  function retry() {
    if (attempt.current !== null) void run(attempt.current);
  }

  return { draft, setDraft, pending, stream, error, submit, retry };
}
