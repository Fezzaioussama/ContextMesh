import type { Dispatch, SetStateAction } from "react";
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
  Page,
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

/**
 * Awaits `request` and hands its result to `done`, or its error as display text to `failed`,
 * unless `signal` aborted meanwhile: a superseded or unmounted request never updates state.
 * A `done` that throws is reported through `failed` too.
 *
 * @returns Whether the request still counts, so the caller can end its loading state.
 */
async function settle<T>(
  signal: AbortSignal,
  request: Promise<T>,
  done: (value: T) => void,
  failed: (message: string) => void,
): Promise<boolean> {
  try {
    const value = await request;
    if (!signal.aborted) done(value);
  } catch (failure) {
    if (!signal.aborted) failed(describeError(failure));
  }
  return !signal.aborted;
}

/** `previous` with `items` merged in by id: known ones replaced in place, new ones appended. */
function mergedById<T extends { id: string }>(previous: T[], items: T[]): T[] {
  const merged = new Map(previous.map((item) => [item.id, item]));
  items.forEach((item) => merged.set(item.id, item));
  return [...merged.values()];
}

export function useMetadata() {
  const [agent, setAgent] = useState<AgentMetadata | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setError("");
    void settle(controller.signal, metadata(controller.signal), setAgent, setError);
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
    const received = (page: Page<Conversation>) => {
      setItems((previous) => (append ? mergedById(previous, page.items) : page.items));
      setCursor(page.next_cursor);
    };
    if (await settle(controller.signal, conversations(next, controller.signal), received, setError))
      setLoading(false);
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

  const refresh = () => load(null, false);
  return { items, cursor, loading, error, insert, refresh, more: () => load(cursor, true) };
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
    const done = (conversation: Conversation) => created(conversation, draft, keepScope);
    try {
      if (await settle(controller.signal, createConversation(controller.signal), done, setError))
        setPending(false);
    } finally {
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
      const request = history(conversationId, controller.signal);
      if (await settle(controller.signal, request, setMessages, setError)) setLoading(false);
    }
    void load();
    return () => controller.abort();
  }, [conversationId, revision]);

  /** Add a finished turn's messages without reloading the conversation. */
  function complete(turn: CompletedTurn) {
    setMessages((previous) => mergedById(previous, [turn.user_message, turn.assistant_message]));
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
  const attempt = useRef(readAttempt(id));
  const { stream, error, busy, cancel, clear, send } = useTurnStream();

  useEffect(() => {
    attempt.current = readAttempt(id);
    setDraft(attempt.current?.message ?? suggestion);
    clear();
    return cancel;
  }, [id, suggestion, clear, cancel]);

  function completed(turn: CompletedTurn) {
    safely(() => sessionStorage.removeItem(attemptKey(id)), undefined);
    attempt.current = null;
    setDraft("");
    onComplete(turn);
  }

  /** Saves the attempt before sending, so a reload can retry it with the same key. */
  function run(current: PendingAttempt) {
    if (busy()) return;
    attempt.current = current;
    safely(() => sessionStorage.setItem(attemptKey(id), JSON.stringify(current)), undefined);
    void send(id, current, completed);
  }

  function submit() {
    const message = draft.trim();
    if (message.length > 0) run(nextAttempt(attempt.current, message, scope));
  }

  function retry() {
    if (attempt.current !== null) run(attempt.current);
  }

  return { draft, setDraft, pending: stream !== null, stream, error, submit, retry };
}

/** The attempt to send: the saved one while message and scope are unchanged, else a new key. */
function nextAttempt(
  previous: PendingAttempt | null,
  message: string,
  scope: string[] | null,
): PendingAttempt {
  const unchanged =
    previous?.message === message &&
    JSON.stringify(previous.sourceIds) === JSON.stringify(scope);
  return unchanged ? previous : { message, sourceIds: scope, key: crypto.randomUUID() };
}

/**
 * The turn in flight, with its streamed draft and its error. One turn runs at a time:
 * callers check `busy()` before `send`, and `cancel` stops a superseded or unmounted turn
 * so it never updates state.
 */
function useTurnStream() {
  const [stream, setStream] = useState<PendingTurn | null>(null);
  const [error, setError] = useState("");
  const active = useRef<AbortController | null>(null);
  const cancel = useCallback(() => {
    active.current?.abort();
    active.current = null;
  }, []);
  const clear = useCallback(() => {
    setStream(null);
    setError("");
  }, []);

  /** Streams one turn to `done`; its draft clears when it ends, unless it was cancelled. */
  async function send(id: string, attempt: PendingAttempt, done: (turn: CompletedTurn) => void) {
    const controller = new AbortController();
    active.current = controller;
    setError("");
    setStream({ message: attempt.message, text: "", progress: "Preparing your answer…" });
    const listener = streamListener(controller.signal, setStream);
    const turn = sendMessage(id, attempt, attempt.key, controller.signal, listener);
    try {
      if (await settle(controller.signal, turn, done, setError)) setStream(null);
    } finally {
      if (active.current === controller) active.current = null;
    }
  }

  return { stream, error, busy: () => active.current !== null, cancel, clear, send };
}

/** Far above the server's answer limit; a longer stream is refused rather than rendered. */
const maxAnswerChars = 2_097_152;

/** Shows a turn's events in its pending draft until `signal` aborts. */
function streamListener(
  signal: AbortSignal,
  show: Dispatch<SetStateAction<PendingTurn | null>>,
): (event: TurnStreamEvent) => void {
  let text = "";
  return (event) => {
    if (signal.aborted) return;
    text = appendedDelta(text, event);
    show((turn) => turn && withEvent(turn, event, text));
  };
}

/** `text` with a delta event's text appended; throws when the answer grows past the limit. */
function appendedDelta(text: string, event: TurnStreamEvent): string {
  const next = event.event === "delta" ? text + event.data.text : text;
  if (next.length > maxAnswerChars) throw new Error("Answer exceeds stream limit");
  return next;
}

/** The pending draft after an event: deltas show `text`, progress replaces the label. */
function withEvent(turn: PendingTurn, event: TurnStreamEvent, text: string): PendingTurn {
  if (event.event === "delta") return { ...turn, text };
  if (event.event === "progress" && event.data.stage !== "working")
    return { ...turn, progress: event.data.message };
  return turn;
}
