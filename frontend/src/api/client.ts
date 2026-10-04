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
  turn_in_progress:
    "This conversation is still processing a message. Wait a moment, then retry.",
  idempotency_conflict:
    "This retry does not match the original message. Start a new message.",
  not_found:
    "This conversation is unavailable. Select another conversation or start a new one.",
  validation_error: "Check your message and try again.",
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

async function request<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(`${base}${path}`, init);
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<T>;
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

export function sendMessage(
  id: string,
  message: string,
  key: string,
  signal: AbortSignal,
): Promise<CompletedTurn> {
  return request(`/conversations/${encodeURIComponent(id)}/messages`, {
    method: "POST",
    signal,
    headers: { "Content-Type": "application/json", "Idempotency-Key": key },
    body: JSON.stringify({ message }),
  });
}
