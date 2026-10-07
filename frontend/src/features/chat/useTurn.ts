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

function sameScope(left: string[] | null, right: string[] | null): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function prepareAttempt(
  previous: PendingAttempt | null,
  message: string,
  sourceIds: string[] | null,
): PendingAttempt {
  if (previous === null) return { message, sourceIds, key: crypto.randomUUID() };
  const unchanged =
    previous.message === message && sameScope(previous.sourceIds, sourceIds);
  if (unchanged) return previous;
  return { message, sourceIds, key: crypto.randomUUID() };
}

export function useTurn(
  id: string,
  suggestion: string,
  scope: string[] | null,
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
    attempt.current = prepareAttempt(attempt.current, message, scope);
    saveAttempt(id, attempt.current);
    setPending(true);
    setError("");
    await execute(attempt.current, controller);
  }

  async function execute(current: PendingAttempt, controller: AbortController) {
    try {
      const turn = await sendMessage(
        id,
        { message: current.message, sourceIds: current.sourceIds },
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
