import type {
  AgentMetadata,
  CompletedTurn,
  Conversation,
  DocumentSummary,
  Evidence,
  Message,
  Page,
  SafeErrorBody,
  Source,
  UploadReceipt,
} from "./contracts";

const base = "/api/v1";
const assistant = `${base}/assistant`;

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
    "This file type is not supported. Upload PDF, Word, PowerPoint, Excel, HTML, Markdown, or text files.",
  payload_too_large: "This file is larger than the server's upload limit.",
  invalid_input: "Check your input and try again.",
  wrong_source_kind: "That action does not apply to this kind of source.",
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

export function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "Could not reach the API. Check that the backend is running, then retry.";
}

async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<T>;
}

async function responseError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as SafeErrorBody;
    return new ApiError(body.error.code, body.error.retryable);
  } catch {
    return new ApiError("unavailable", true);
  }
}

function postJson<T>(
  url: string,
  body: unknown,
  signal?: AbortSignal,
  headers: Record<string, string> = {},
): Promise<T> {
  return request(url, {
    method: "POST",
    signal,
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
}

const id = encodeURIComponent;

// Assistant

export function metadata(signal: AbortSignal): Promise<AgentMetadata> {
  return request(assistant, { signal });
}

export function conversations(
  cursor: string | null,
  signal: AbortSignal,
): Promise<Page<Conversation>> {
  const query = new URLSearchParams({ limit: "20" });
  if (cursor !== null) query.set("cursor", cursor);
  return request(`${assistant}/conversations?${query}`, { signal });
}

/** Created untitled: the server names it after its first question. */
export function createConversation(signal: AbortSignal): Promise<Conversation> {
  return postJson(`${assistant}/conversations`, {}, signal);
}

/** Every message of a conversation, following page cursors to the end. */
export async function history(
  conversationId: string,
  signal: AbortSignal,
): Promise<Message[]> {
  const items: Message[] = [];
  let cursor: string | null = null;
  do {
    const query = new URLSearchParams({ limit: "100" });
    if (cursor !== null) query.set("cursor", cursor);
    const path = `${assistant}/conversations/${id(conversationId)}/messages?${query}`;
    const page: Page<Message> = await request(path, { signal });
    items.push(...page.items);
    cursor = page.next_cursor;
  } while (cursor !== null);
  return items;
}

export interface TurnRequest {
  message: string;
  /** null searches every eligible source. */
  sourceIds: string[] | null;
}

export function sendMessage(
  conversationId: string,
  turn: TurnRequest,
  key: string,
  signal: AbortSignal,
): Promise<CompletedTurn> {
  const body =
    turn.sourceIds === null
      ? { message: turn.message }
      : { message: turn.message, source_ids: turn.sourceIds };
  const path = `${assistant}/conversations/${id(conversationId)}/messages`;
  return postJson(path, body, signal, { "Idempotency-Key": key });
}

// Sources

export async function sources(signal: AbortSignal): Promise<Source[]> {
  const page = await request<{ items: Source[] }>(`${base}/sources`, { signal });
  return page.items;
}

export interface SourceRequest {
  name: string;
  description: string;
  /** A URL registers a website to crawl; without one the source holds uploaded files. */
  url: string | null;
}

export function createSource(source: SourceRequest): Promise<Source> {
  return postJson(`${base}/sources`, source);
}

export function syncSource(sourceId: string): Promise<{ job_id: string }> {
  return request(`${base}/sources/${id(sourceId)}/sync`, { method: "POST" });
}

export function deleteSource(sourceId: string): Promise<{ job_id: string }> {
  return request(`${base}/sources/${id(sourceId)}`, { method: "DELETE" });
}

export async function sourceDocuments(
  sourceId: string,
  signal: AbortSignal,
): Promise<DocumentSummary[]> {
  const path = `${base}/sources/${id(sourceId)}/documents`;
  const page = await request<{ items: DocumentSummary[] }>(path, { signal });
  return page.items;
}

export function uploadDocument(sourceId: string, file: File): Promise<UploadReceipt> {
  const form = new FormData();
  form.append("file", file);
  return request(`${base}/sources/${id(sourceId)}/documents`, {
    method: "POST",
    body: form,
  });
}

export function deleteDocument(documentId: string): Promise<{ job_id: string }> {
  return request(`${base}/documents/${id(documentId)}`, { method: "DELETE" });
}

export function evidence(path: string, signal: AbortSignal): Promise<Evidence> {
  if (!path.startsWith(`${base}/documents/`))
    return Promise.reject(new Error("Unexpected evidence path."));
  return request(path, { signal });
}
