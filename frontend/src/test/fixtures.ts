import type { AgentMetadata, Conversation, Message } from "../api/contracts";

export const agent: AgentMetadata = {
  name: "Foundation Assistant",
  mode: "provider_chat",
  provider: "openai",
  model: "gpt-4.1-mini",
  configured: true,
  retrieval_enabled: false,
  limits: {
    max_message_chars: 8000,
    max_history_messages: 20,
    max_output_tokens: 1024,
  },
};

export const first: Conversation = {
  id: "0ed036d2-69f2-4f29-a2bd-5a50d62047dd",
  title: "Planning notes",
  created_at: "2026-10-03T12:00:00Z",
  updated_at: "2026-10-03T12:00:00Z",
};
export const second: Conversation = {
  ...first,
  id: "8f9a4f26-3626-4664-8b84-785a6bccca98",
  title: "Another conversation",
};

export function message(
  id: string,
  role: Message["role"],
  content: string,
): Message {
  return {
    id,
    role,
    content,
    turn_id: "f197026c-864c-40c3-89fc-a140a181ad00",
    created_at: "2026-10-03T12:00:00Z",
  };
}

export function turn(content = "A useful answer.") {
  return {
    turn_id: "turn-id",
    conversation_id: first.id,
    mode: "provider_chat",
    user_message: message("user", "user", "Help me plan"),
    assistant_message: message("assistant", "assistant", content),
    usage: { input_tokens: 4, output_tokens: 8 },
    trace: [{ stage: "respond", summary: "Generated a reply." }],
  };
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export type HttpHandler = (url: string, init: RequestInit) => Promise<Response>;

export function createHttp(handler: HttpHandler, configured = true) {
  return async function fetchBoundary(
    input: string | URL | Request,
    init: RequestInit = {},
  ) {
    const url = String(input);
    if (url === "/api/v1/assistant") return json({ ...agent, configured });
    return handleCollections(url, init, handler);
  };
}

async function handleCollections(
  url: string,
  init: RequestInit,
  handler: HttpHandler,
) {
  if (url.startsWith("/api/v1/assistant/conversations?"))
    return json({ items: [first, second], next_cursor: null });
  if (url === "/api/v1/assistant/conversations") return json(first, 201);
  return handler(url, init);
}

export async function emptyHistory() {
  return json({ items: [], next_cursor: null });
}

export function selectConversation(id = first.id) {
  localStorage.setItem("contextmesh.selected-conversation", id);
}
