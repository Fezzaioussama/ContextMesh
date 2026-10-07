import type {
  AgentMetadata,
  CompletedTurn,
  Conversation,
  Message,
  Page,
  SafeErrorBody,
} from "./contracts";

const base = "/api/v1/assistant";

const errorMessages: Record<string, string> = {
  database_unavailable:
    "The database is unavailable. Check the backend connection, then retry.",
  provider_unavailable:
    "The model provider could not respond. Your message is saved for retry.",
  provider_not_configured:
    "Configure the model provider on the server to send messages.",
  model_output_invalid:
    "The model returned an unusable response. Your message is saved for retry.",
  model_output_limit:
    "The model ran out of output tokens. Ask the operator to raise the server's output budget.",
  agent_budget_exceeded:
    "The agent reached its token budget. Narrow the question or its source scope.",
  embedding_input_rejected:
    "The embedding provider rejected this text. Shorten or rephrase it.",
  agent_deadline_exceeded:
    "The agent ran out of time. Retry, or narrow the question or source scope.",
  evidence_changed:
    "Sources changed while the answer was prepared. Retry the message.",
  turn_in_progress:
    "This conversation is still processing a message. Wait a moment, then retry.",
  idempotency_conflict:
    "This retry does not match the original message. Start a new message.",
  not_found:
    "This item is unavailable. Refresh, then select another conversation or source.",
  forbidden: "Your role does not permit changing sources.",
  unsupported_media_type:
    "Upload Markdown (.md, .markdown) or plain text (.txt) files.",
  payload_too_large: "This file is larger than the server's upload limit.",
  invalid_input: "Check your input and try again.",
};

export class ApiError extends Error {
  constructor(
    readonly code: string,
    readonly retryable: boolean,
  ) {
    super(
      errorMessages[code] ??
        "The request could not be completed. Please try again.",
    );
  }
}

async function responseError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as SafeErrorBody;
    return new ApiError(body.error.code, body.error.retryable);
  } catch {
    return new ApiError("unavailable", true);
  }
}

export async function requestJson<T>(url: string, init: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<T>;
}

function request<T>(path: string, init: RequestInit): Promise<T> {
  return requestJson(`${base}${path}`, init);
}

export function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "Could not reach the API. Check that the backend is running, then retry.";
}

export function isCancelled(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export function metadata(signal: AbortSignal): Promise<AgentMetadata> {
  return request("", { signal });
}

export function conversations(
  cursor: string | null,
  signal: AbortSignal,
): Promise<Page<Conversation>> {
  const query = new URLSearchParams({ limit: "20" });
  if (cursor !== null) query.set("cursor", cursor);
  return request(`/conversations?${query}`, { signal });
}

export function createConversation(
  title: string,
  signal: AbortSignal,
): Promise<Conversation> {
  return request("/conversations", {
    method: "POST",
    signal,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
}

async function messagePage(
  id: string,
  cursor: string | null,
  signal: AbortSignal,
): Promise<Page<Message>> {
  const query = new URLSearchParams({ limit: "100" });
  if (cursor !== null) query.set("cursor", cursor);
  return request(`/conversations/${encodeURIComponent(id)}/messages?${query}`, {
    signal,
  });
}

export async function history(
  id: string,
  signal: AbortSignal,
): Promise<Message[]> {
  let cursor: string | null = null;
  const items: Message[] = [];
  do {
    const page = await messagePage(id, cursor, signal);
    items.push(...page.items);
    cursor = page.next_cursor;
  } while (cursor !== null);
  return items;
}

export interface TurnRequest {
  message: string;
  sourceIds: string[] | null;
}

function turnBody(turn: TurnRequest): string {
  if (turn.sourceIds === null) return JSON.stringify({ message: turn.message });
  return JSON.stringify({ message: turn.message, source_ids: turn.sourceIds });
}

export function sendMessage(
  id: string,
  turn: TurnRequest,
  key: string,
  signal: AbortSignal,
): Promise<CompletedTurn> {
  return request(`/conversations/${encodeURIComponent(id)}/messages`, {
    method: "POST",
    signal,
    headers: { "Content-Type": "application/json", "Idempotency-Key": key },
    body: turnBody(turn),
  });
}
