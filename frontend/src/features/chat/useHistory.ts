import { useEffect, useState } from "react";
import { describeError, history } from "../../api/client";
import type { CompletedTurn, Message } from "../../api/contracts";

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
        finish(controller.signal);
      }
    }
    void load();
    return () => controller.abort();
  }, [conversationId, revision]);

  function finish(signal: AbortSignal) {
    if (!signal.aborted) setLoading(false);
  }

  function complete(turn: CompletedTurn) {
    setMessages((previous) => mergeTurn(previous, turn));
  }

  return {
    messages,
    loading,
    error,
    complete,
    refresh: () => setRevision((value) => value + 1),
  };
}

function mergeTurn(previous: Message[], turn: CompletedTurn): Message[] {
  const messages = new Map(previous.map((message) => [message.id, message]));
  messages.set(turn.user_message.id, turn.user_message);
  messages.set(turn.assistant_message.id, turn.assistant_message);
  return [...messages.values()];
}
