import { useEffect, useRef, useState } from "react";
import { describeError, sendMessage } from "../../api/client";
import type { CompletedTurn } from "../../api/contracts";
import { clearAttempt, readAttempt, saveAttempt } from "./browserState";
import type { PendingAttempt } from "./browserState";

function initialDraft(id: string, suggestion: string): string {
  const attempt = readAttempt(id);
  if (attempt !== null) return attempt.message;
  return suggestion;
}

function prepareAttempt(
  previous: PendingAttempt | null,
  message: string,
): PendingAttempt {
  if (previous !== null && previous.message === message) return previous;
  return { message, key: crypto.randomUUID() };
}

export function useTurn(
  id: string,
  suggestion: string,
  onComplete: (turn: CompletedTurn) => void,
) {
  const [draft, setDraft] = useState(() => initialDraft(id, suggestion));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const active = useRef<AbortController | null>(null);
  const attempt = useRef(readAttempt(id));

  useEffect(
    () => () => {
      if (active.current !== null) active.current.abort();
    },
    [],
  );

  function settle(turn: CompletedTurn, signal: AbortSignal) {
    if (signal.aborted) return;
    clearAttempt(id);
    attempt.current = null;
    setDraft("");
    onComplete(turn);
  }

  async function submit() {
    if (active.current !== null) return;
    const message = draft.trim();
    if (message.length === 0) return;
    const controller = new AbortController();
    active.current = controller;
    attempt.current = prepareAttempt(attempt.current, message);
    saveAttempt(id, attempt.current);
    setPending(true);
    setError("");
    await execute(attempt.current, controller);
  }

  async function execute(current: PendingAttempt, controller: AbortController) {
    try {
      const turn = await sendMessage(
        id,
        current.message,
        current.key,
        controller.signal,
      );
      settle(turn, controller.signal);
    } catch (failure) {
      if (!controller.signal.aborted) setError(describeError(failure));
    } finally {
      if (!controller.signal.aborted) setPending(false);
      active.current = null;
    }
  }

  return { draft, setDraft, pending, error, submit };
}
