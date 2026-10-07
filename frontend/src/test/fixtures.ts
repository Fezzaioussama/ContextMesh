import type {
  AgentMetadata,
  Answer,
  Conversation,
  DocumentSummary,
  Job,
  Message,
  Source,
} from "../api/contracts";

export const agent: AgentMetadata = {
  name: "ContextMesh Agent",
  mode: "agentic_rag",
  provider: "openai",
  model: "gpt-4.1-mini",
  embedding_model: "text-embedding-3-small",
  configured: true,
  retrieval_enabled: true,
  supported_media_types: [".md", ".pdf", ".docx", ".txt"],
  limits: {
    max_message_chars: 8000,
    max_history_messages: 20,
    max_output_tokens: 2048,
    max_retrieval_rounds: 3,
    max_query_variants: 2,
    max_repairs: 1,
    deadline_seconds: 60,
    max_upload_bytes: 2000000,
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

export const handbook: Source = {
  id: "9a3c7a49-8c39-4f53-9f42-4f4c1b2bd0a1",
  kind: "upload",
  name: "Handbook",
  description: "Platform handbook",
  url: null,
  document_count: 1,
  searchable_count: 1,
  latest_sync: null,
  created_at: "2026-10-03T12:00:00Z",
};
export const decisions: Source = {
  ...handbook,
  id: "4d2b9c4e-0d2f-4a8e-9b66-0f7f0a9f6c11",
  name: "Decisions",
};

export function indexJob(overrides: Partial<Job> = {}): Job {
  return {
    id: "c4f1",
    kind: "document.index_requested",
    status: "succeeded",
    attempts: 1,
    stage: "published",
    error_code: null,
    updated_at: "2026-10-03T12:00:00Z",
    ...overrides,
  };
}

export function documentSummary(
  overrides: Partial<DocumentSummary> = {},
): DocumentSummary {
  return {
    id: "2b5f3a8e-58c4-4c25-9a3e-6d8a0c3a9e10",
    source_id: handbook.id,
    title: "auth.md",
    media_type: "text/markdown",
    searchable: true,
    chunk_count: 3,
    latest_job: indexJob(),
    uri: null,
    updated_at: "2026-10-03T12:00:00Z",
    ...overrides,
  };
}

export const groundedAnswer: Answer = {
  status: "partial",
  claims: [
    { id: "claim-1", text: "We use OIDC.", citation_ids: ["citation-1"] },
  ],
  citations: [
    {
      id: "citation-1",
      number: 1,
      source_id: handbook.id,
      document_id: "doc",
      document_version_id: "version",
      index_generation_id: "generation",
      chunk_id: "chunk",
      title: "auth.md",
      locator: {
        heading_path: ["Auth", "Decision"],
        line_start: 3,
        line_end: 4,
        page: null,
        slide: null,
      },
      snippet: "We use OIDC with a central identity provider.",
      source_url: null,
      evidence_path: "/api/v1/documents/doc/versions/version/evidence?chunk_id=chunk",
    },
  ],
  gaps: ["The rollout date is not documented."],
};

export function message(
  id: string,
  role: Message["role"],
  content: string,
  answer: Answer | null = null,
): Message {
  return {
    id,
    role,
    content,
    answer,
    trace: [],
    turn_id: "f197026c-864c-40c3-89fc-a140a181ad00",
    created_at: "2026-10-03T12:00:00Z",
  };
}

export function turn(content = "A useful answer.", answer: Answer | null = null) {
  return {
    turn_id: "turn-id",
    conversation_id: first.id,
    mode: "agentic_rag",
    user_message: message("user", "user", "Help me plan"),
    assistant_message: message("assistant", "assistant", content, answer),
    usage: { input_tokens: 4, output_tokens: 8 },
    trace: [{ stage: "release", summary: "Released 1 supported claim." }],
  };
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export type HttpHandler = (url: string, init: RequestInit) => Promise<Response>;

export function createHttp(
  handler: HttpHandler,
  configured = true,
  sources: Source[] = [],
) {
  return async function fetchBoundary(
    input: string | URL | Request,
    init: RequestInit = {},
  ) {
    const url = String(input);
    if (url === "/api/v1/assistant") return json({ ...agent, configured });
    return listSources(url, init, sources) ?? handleCollections(url, init, handler);
  };
}

function listSources(url: string, init: RequestInit, sources: Source[]) {
  if (url === "/api/v1/sources" && init.method === undefined)
    return json({ items: sources });
  return null;
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
