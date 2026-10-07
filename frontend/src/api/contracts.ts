export interface AgentMetadata {
  name: string;
  mode: "agentic_rag";
  provider: string;
  model: string;
  embedding_model: string;
  configured: boolean;
  retrieval_enabled: boolean;
  supported_media_types: string[];
  limits: {
    max_message_chars: number;
    max_history_messages: number;
    max_output_tokens: number;
    max_retrieval_rounds: number;
    max_query_variants: number;
    max_repairs: number;
    deadline_seconds: number;
    max_upload_bytes: number;
  };
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface Locator {
  heading_path: string[];
  line_start: number;
  line_end: number;
}

export interface Citation {
  id: string;
  number: number;
  source_id: string;
  document_id: string;
  document_version_id: string;
  index_generation_id: string;
  chunk_id: string;
  title: string;
  locator: Locator;
  snippet: string;
  evidence_path: string;
}

export interface Claim {
  id: string;
  text: string;
  citation_ids: string[];
}

export type AnswerStatus =
  | "answered"
  | "partial"
  | "insufficient_evidence"
  | "withheld";

export interface Answer {
  status: AnswerStatus;
  claims: Claim[];
  citations: Citation[];
  gaps: string[];
}

export interface TraceStage {
  stage: string;
  summary: string;
}

export interface Message {
  id: string;
  turn_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  answer: Answer | null;
  trace: TraceStage[];
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface CompletedTurn {
  turn_id: string;
  conversation_id: string;
  mode: "agentic_rag";
  user_message: Message;
  assistant_message: Message;
  usage: { input_tokens: number; output_tokens: number };
  trace: TraceStage[];
}

export interface Source {
  id: string;
  kind: "upload";
  name: string;
  description: string;
  document_count: number;
  searchable_count: number;
  created_at: string;
}

export type JobStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface Job {
  id: string;
  kind: string;
  status: JobStatus;
  attempts: number;
  stage: string | null;
  error_code: string | null;
  updated_at: string;
}

export interface DocumentSummary {
  id: string;
  source_id: string;
  title: string;
  media_type: string;
  searchable: boolean;
  chunk_count: number;
  latest_job: Job | null;
  updated_at: string;
}

export interface UploadReceipt {
  document: DocumentSummary;
  job_id: string;
  duplicate: boolean;
}

export interface Evidence {
  chunk_id: string;
  source_name: string;
  title: string;
  locator: Locator;
  text: string;
}

export interface SafeErrorBody {
  error: {
    code: string;
    message: string;
    request_id: string;
    retryable: boolean;
  };
}
