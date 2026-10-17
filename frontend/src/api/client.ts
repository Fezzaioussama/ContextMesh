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
  TurnStreamEvent,
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
  stream_interrupted: "The answer stream was interrupted. Your message is saved for retry.",
  stream_invalid: "The answer stream could not be read. Your message is saved for retry.",
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

/**
 * Sends a turn through the streaming endpoint and resolves with the saved turn.
 *
 * `onEvent` receives progress and draft text as they arrive; drafts are provisional and
 * must be replaced by the resolved turn. A plain JSON turn response is accepted too.
 * Throws `ApiError` on an HTTP error, a stream `error`, or an interrupted or malformed
 * stream, and an `AbortError` when `signal` aborts.
 */
export async function sendMessage(
  conversationId: string,
  turn: TurnRequest,
  key: string,
  signal: AbortSignal,
  onEvent: (event: TurnStreamEvent) => void = () => {},
): Promise<CompletedTurn> {
  const body =
    turn.sourceIds === null
      ? { message: turn.message }
      : { message: turn.message, source_ids: turn.sourceIds };
  const path = `${assistant}/conversations/${id(conversationId)}/messages/stream`;
  const response = await fetch(path, {
    method: "POST",
    body: JSON.stringify(body),
    signal,
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      "Idempotency-Key": key,
    },
  });
  if (!response.ok) throw await responseError(response);
  const completed: unknown = response.headers.get("Content-Type")?.includes("application/json")
    ? await response.json()
    : await readTurnStream(response, signal, onEvent);
  if (!isCompletedTurn(completed) || completed.conversation_id !== conversationId)
    throw new ApiError("stream_invalid", true);
  return completed;
}

const maxStreamFrame = 1_048_576;

/** SSE frames are bounded independently of network chunks and decoded as UTF-8. */
class TurnStreamParser {
  private buffer = "";

  push(text: string): TurnStreamEvent[] {
    this.buffer += text;
    const events: TurnStreamEvent[] = [];
    let boundary = /\r\n\r\n|\n\n|\r\r/.exec(this.buffer);
    while (boundary !== null) {
      if (boundary.index > maxStreamFrame) throw new ApiError("stream_invalid", true);
      const event = parseStreamFrame(this.buffer.slice(0, boundary.index));
      if (event !== null) events.push(event);
      this.buffer = this.buffer.slice(boundary.index + boundary[0].length);
      boundary = /\r\n\r\n|\n\n|\r\r/.exec(this.buffer);
    }
    if (this.buffer.length > maxStreamFrame) throw new ApiError("stream_invalid", true);
    return events;
  }
}

function parseStreamFrame(frame: string): TurnStreamEvent | null {
  let event = "message";
  const data: string[] = [];
  for (const line of frame.split(/\r\n|\r|\n/)) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
  }
  if (data.length === 0) return null;
  if (!["progress", "delta", "completed", "error"].includes(event)) return null;
  try {
    return validatedStreamEvent(event, JSON.parse(data.join("\n")));
  } catch {
    throw new ApiError("stream_invalid", true);
  }
}

function validatedStreamEvent(event: string, data: Record<string, unknown>): TurnStreamEvent {
  if (data === null || typeof data !== "object") throw new Error("Invalid event");
  const text = (key: string) => typeof data[key] === "string";
  const valid: Record<string, boolean> = {
    progress: text("stage") && text("message"),
    delta: text("text"),
    completed: isCompletedTurn(data),
    error: text("code") && text("message") && typeof data.retryable === "boolean",
  };
  if (!valid[event]) throw new Error("Invalid event");
  return { event, data } as TurnStreamEvent;
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function stringFields(value: Record<string, unknown>, fields: string[]): boolean {
  return fields.every((field) => typeof value[field] === "string");
}

function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function nonnegativeInteger(value: unknown): boolean {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function validTrace(value: unknown): boolean {
  return Array.isArray(value) && value.every((stage) =>
    record(stage) && stringFields(stage, ["stage", "summary"]));
}

function validLocator(value: unknown): boolean {
  if (!record(value)) return false;
  return strings(value.heading_path) &&
    nonnegativeInteger(value.line_start) && nonnegativeInteger(value.line_end) &&
    (value.page === null || nonnegativeInteger(value.page)) &&
    (value.slide === null || nonnegativeInteger(value.slide));
}

function validCitation(value: unknown): boolean {
  if (!record(value)) return false;
  return stringFields(value, ["id", "source_id", "document_id", "document_version_id",
    "index_generation_id", "chunk_id", "title", "snippet", "evidence_path"]) &&
    nonnegativeInteger(value.number) && value.number !== 0 &&
    validLocator(value.locator) &&
    (value.source_url === null || typeof value.source_url === "string");
}

function validClaim(value: unknown): boolean {
  return record(value) && stringFields(value, ["id", "text"]) && strings(value.citation_ids);
}

function validAnswer(value: unknown): boolean {
  if (value === null) return true;
  if (!record(value)) return false;
  return ["direct", "answered", "partial", "insufficient_evidence", "withheld"].includes(String(value.status)) &&
    strings(value.gaps) && Array.isArray(value.claims) && value.claims.every(validClaim) &&
    Array.isArray(value.citations) && value.citations.every(validCitation);
}

function validMessage(value: unknown, role: string): boolean {
  if (!record(value)) return false;
  return value.role === role &&
    stringFields(value, ["id", "turn_id", "content", "created_at"]) &&
    !Number.isNaN(Date.parse(String(value.created_at))) &&
    validAnswer(value.answer) && validTrace(value.trace);
}

function isCompletedTurn(data: unknown): data is CompletedTurn {
  if (!record(data)) return false;
  return stringFields(data, ["turn_id", "conversation_id"]) && data.mode === "agentic_rag" &&
    validMessage(data.user_message, "user") && validMessage(data.assistant_message, "assistant") &&
    validTrace(data.trace) && record(data.usage) &&
    nonnegativeInteger(data.usage.input_tokens) && nonnegativeInteger(data.usage.output_tokens);
}

async function readTurnStream(
  response: Response,
  signal: AbortSignal,
  onEvent: (event: TurnStreamEvent) => void,
): Promise<CompletedTurn> {
  if (response.body === null) throw new ApiError("stream_interrupted", true);
  const reader = response.body.getReader();
  const parser = new TurnStreamParser();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  const abort = () => { void reader.cancel().catch(() => {}); };
  signal.addEventListener("abort", abort, { once: true });
  try {
    while (!signal.aborted) {
      const chunk = await reader.read();
      if (chunk.done) break;
      for (const event of parser.push(decoder.decode(chunk.value, { stream: true }))) {
        if (signal.aborted) throw new DOMException("Aborted", "AbortError");
        if (event.event === "error") throw new ApiError(event.data.code, event.data.retryable);
        if (event.event === "completed") return event.data;
        onEvent(event);
      }
    }
    throw new ApiError("stream_interrupted", true);
  } finally {
    signal.removeEventListener("abort", abort);
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
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
