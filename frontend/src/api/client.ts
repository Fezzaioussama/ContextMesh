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
  const path = `${assistant}/conversations/${id(conversationId)}/messages/stream`;
  const response = await fetch(path, {
    method: "POST",
    body: JSON.stringify(turnBody(turn)),
    signal,
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      "Idempotency-Key": key,
    },
  });
  const completed = await responseTurn(response, signal, onEvent);
  if (!isCompletedTurn(completed) || completed.conversation_id !== conversationId)
    throw new ApiError("stream_invalid", true);
  return completed;
}

/** The request body; a null scope omits `source_ids`, so every eligible source is searched. */
function turnBody(turn: TurnRequest): { message: string; source_ids?: string[] } {
  if (turn.sourceIds === null) return { message: turn.message };
  return { message: turn.message, source_ids: turn.sourceIds };
}

/** The unvalidated turn a response carries, read from its SSE stream or its JSON body. */
async function responseTurn(
  response: Response,
  signal: AbortSignal,
  onEvent: (event: TurnStreamEvent) => void,
): Promise<unknown> {
  if (!response.ok) throw await responseError(response);
  if (response.headers.get("Content-Type")?.includes("application/json")) return response.json();
  return readTurnStream(response, signal, onEvent);
}

// Response validation: the stream is untrusted input until every field has the right shape.

/** A check that an untrusted value has the expected shape. */
type Check = (value: unknown) => boolean;

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** An object whose listed fields each pass their check; other fields are ignored. */
function shape(fields: Record<string, Check>): Check {
  return (value) =>
    record(value) && Object.entries(fields).every(([field, check]) => check(value[field]));
}

function listOf(check: Check): Check {
  return (value) => Array.isArray(value) && value.every(check);
}

function nullable(check: Check): Check {
  return (value) => value === null || check(value);
}

function oneOf(...allowed: unknown[]): Check {
  return (value) => allowed.includes(value);
}

const isString: Check = (value) => typeof value === "string";
const isBoolean: Check = (value) => typeof value === "boolean";
const isCount: Check = (value) => Number.isInteger(value) && (value as number) >= 0;
const isPositive: Check = (value) => Number.isInteger(value) && (value as number) > 0;
const isTimestamp: Check = (value) => isString(value) && !Number.isNaN(Date.parse(value as string));

const isTrace = listOf(shape({ stage: isString, summary: isString }));

const isLocator = shape({
  heading_path: listOf(isString),
  line_start: isCount,
  line_end: isCount,
  page: nullable(isCount),
  slide: nullable(isCount),
});

const isCitation = shape({
  id: isString,
  number: isPositive,
  source_id: isString,
  document_id: isString,
  document_version_id: isString,
  index_generation_id: isString,
  chunk_id: isString,
  title: isString,
  snippet: isString,
  evidence_path: isString,
  locator: isLocator,
  source_url: nullable(isString),
});

const isAnswer = nullable(shape({
  status: oneOf("direct", "answered", "partial", "insufficient_evidence", "withheld"),
  gaps: listOf(isString),
  claims: listOf(shape({ id: isString, text: isString, citation_ids: listOf(isString) })),
  citations: listOf(isCitation),
}));

function isMessage(role: string): Check {
  return shape({
    role: oneOf(role),
    id: isString,
    turn_id: isString,
    content: isString,
    created_at: isTimestamp,
    answer: isAnswer,
    trace: isTrace,
  });
}

/** The `completed` payload: a saved turn with both messages, its trace, and its usage. */
const isCompletedTurn = shape({
  turn_id: isString,
  conversation_id: isString,
  mode: oneOf("agentic_rag"),
  user_message: isMessage("user"),
  assistant_message: isMessage("assistant"),
  trace: isTrace,
  usage: shape({ input_tokens: isCount, output_tokens: isCount }),
}) as (value: unknown) => value is CompletedTurn;

/** The data each stream event must carry; other event names are ignored, as SSE allows. */
const streamEvents = new Map<string, Check>([
  ["progress", shape({ stage: isString, message: isString })],
  ["delta", shape({ text: isString })],
  ["completed", isCompletedTurn],
  ["error", shape({ code: isString, message: isString, retryable: isBoolean })],
]);

// Stream reading

const maxStreamFrame = 1_048_576;
const frameBoundary = /\r\n\r\n|\n\n|\r\r/;

/** SSE frames are bounded independently of network chunks and decoded as UTF-8. */
class TurnStreamParser {
  private buffer = "";

  /** The events completed by `text`; an unterminated frame waits for the next chunk. */
  push(text: string): TurnStreamEvent[] {
    const frames = (this.buffer + text).split(frameBoundary);
    this.buffer = frames.pop() ?? "";
    if ([...frames, this.buffer].some((frame) => frame.length > maxStreamFrame))
      throw new ApiError("stream_invalid", true);
    return frames
      .map(parseStreamFrame)
      .filter((event): event is TurnStreamEvent => event !== null);
  }
}

/** One frame's event, or null for a comment, an empty frame, or an unknown event name. */
function parseStreamFrame(frame: string): TurnStreamEvent | null {
  const { event, data } = frameFields(frame);
  const valid = streamEvents.get(event);
  if (data.length === 0 || valid === undefined) return null;
  const payload = parsedData(data);
  if (!valid(payload)) throw new ApiError("stream_invalid", true);
  return { event, data: payload } as TurnStreamEvent;
}

/** A frame's event name (the last `event:` line, else `message`) and its `data:` lines. */
function frameFields(frame: string): { event: string; data: string[] } {
  const lines = frame.split(/\r\n|\r|\n/);
  const field = (name: string) =>
    lines.filter((line) => line.startsWith(name)).map((line) => line.slice(name.length));
  const names = field("event:").map((name) => name.trim());
  const event = names.length > 0 ? names[names.length - 1] : "message";
  return { event, data: field("data:").map((line) => line.replace(/^ /, "")) };
}

function parsedData(lines: string[]): unknown {
  try {
    return JSON.parse(lines.join("\n"));
  } catch {
    throw new ApiError("stream_invalid", true);
  }
}

/** Reads the stream to its `completed` turn, always releasing the reader. */
async function readTurnStream(
  response: Response,
  signal: AbortSignal,
  onEvent: (event: TurnStreamEvent) => void,
): Promise<CompletedTurn> {
  if (response.body === null) throw new ApiError("stream_interrupted", true);
  const reader = response.body.getReader();
  const abort = () => { void reader.cancel().catch(() => {}); };
  signal.addEventListener("abort", abort, { once: true });
  try {
    return await completedTurn(reader, signal, onEvent);
  } finally {
    signal.removeEventListener("abort", abort);
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

/** Forwards events until `completed`; a stream that ends first was interrupted. */
async function completedTurn(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  signal: AbortSignal,
  onEvent: (event: TurnStreamEvent) => void,
): Promise<CompletedTurn> {
  const parser = new TurnStreamParser();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  for (let chunk = await reader.read(); !chunk.done; chunk = await reader.read()) {
    for (const event of parser.push(decoder.decode(chunk.value, { stream: true }))) {
      const completed = handledEvent(event, signal, onEvent);
      if (completed !== null) return completed;
    }
  }
  throw new ApiError("stream_interrupted", true);
}

/** The completed turn, or null after forwarding a progress or delta event. */
function handledEvent(
  event: TurnStreamEvent,
  signal: AbortSignal,
  onEvent: (event: TurnStreamEvent) => void,
): CompletedTurn | null {
  if (signal.aborted) throw new DOMException("Aborted", "AbortError");
  if (event.event === "error") throw new ApiError(event.data.code, event.data.retryable);
  if (event.event === "completed") return event.data;
  onEvent(event);
  return null;
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
