import type { DocumentSummary } from "../../api/contracts";

export type StatusTone = "ready" | "busy" | "error" | "idle";

export interface StatusView {
  label: string;
  tone: StatusTone;
}

const jobErrors: Record<string, string> = {
  invalid_encoding: "the file is not UTF-8 text",
  no_text: "no text was found",
  document_too_large: "the document has too many passages",
  provider_not_configured: "configure the model provider, then upload again",
  attempts_exhausted: "retries ran out; upload the file again",
  content_unavailable: "stored content is missing; upload the file again",
  unsupported_media_type: "this file type is not supported",
  embedding_input_rejected: "the embedding provider rejected this text",
};

export function isPending(document: DocumentSummary): boolean {
  const status = document.latest_job?.status;
  return status === "queued" || status === "running";
}

function isFailed(document: DocumentSummary): boolean {
  return document.latest_job?.status === "failed";
}

export function passages(count: number): string {
  return count === 1 ? "1 passage" : `${count} passages`;
}

function busyStatus(document: DocumentSummary): StatusView {
  if (document.latest_job?.error_code) return { label: "Retrying…", tone: "busy" };
  const label = document.searchable ? "Updating index…" : "Indexing…";
  return { label, tone: "busy" };
}

function failedStatus(document: DocumentSummary): StatusView {
  const reason = jobErrors[document.latest_job?.error_code ?? ""] ?? "indexing failed";
  return { label: `Failed: ${reason}`, tone: "error" };
}

function settledStatus(document: DocumentSummary): StatusView {
  if (document.searchable)
    return { label: `Ready · ${passages(document.chunk_count)}`, tone: "ready" };
  return { label: "Not indexed", tone: "idle" };
}

export function documentStatus(document: DocumentSummary): StatusView {
  if (isPending(document)) return busyStatus(document);
  if (isFailed(document)) return failedStatus(document);
  return settledStatus(document);
}
