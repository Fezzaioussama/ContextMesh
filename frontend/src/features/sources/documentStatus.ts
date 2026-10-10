import type { DocumentSummary, Job } from "../../api/contracts";

export type StatusTone = "ready" | "busy" | "error" | "idle";

export interface StatusView {
  label: string;
  tone: StatusTone;
}

const jobErrors: Record<string, string> = {
  invalid_encoding: "the file is not UTF-8 text",
  no_text: "no text was found",
  ocr_required: "it has no text layer (scanned); OCR is not supported",
  invalid_document: "the file is damaged or not what its extension says",
  encrypted_document: "the file is password-protected",
  document_too_complex: "the file expands too far to process safely",
  document_too_large: "the document has too many pages or passages",
  provider_not_configured: "configure the model provider, then upload again",
  attempts_exhausted: "retries ran out; try again",
  content_unavailable: "stored content is missing; upload the file again",
  unsupported_media_type: "this file type is not supported",
  embedding_input_rejected: "the embedding provider rejected this text",
  blocked_destination: "the address is private or not allowed",
  not_found: "the page was not found",
  http_error: "the site returned an error",
  fetch_timeout: "the site did not respond in time",
  fetch_failed: "the site could not be reached",
  page_too_large: "the page is larger than the limit",
  unsupported_content: "the page is not HTML, text, or PDF",
  too_many_redirects: "the page redirects too many times",
  dns_failed: "the site's address could not be found",
  invalid_url: "the page address is not valid",
  robots_disallowed: "the site's robots.txt does not allow crawling this page",
  robots_unreachable: "the site's robots.txt could not be read (the site may be down)",
  no_pages: "no readable page was found at this address",
};

export function isOpen(job: Job | null): boolean {
  return job?.status === "queued" || job?.status === "running";
}

export function isPending(document: DocumentSummary): boolean {
  return isOpen(document.latest_job);
}

function failureReason(code: string | null): string {
  return jobErrors[code ?? ""] ?? "it failed";
}

/** Plain-language crawl status for a website source's latest sync job. */
export function crawlStatus(job: Job | null): StatusView {
  if (job === null) return { label: "Not crawled yet", tone: "idle" };
  if (isOpen(job)) return { label: "Crawling the site…", tone: "busy" };
  if (job.status === "failed")
    return { label: `Crawl failed: ${failureReason(job.error_code)}`, tone: "error" };
  if (job.status === "cancelled") return { label: "Crawl cancelled", tone: "idle" };
  // A partial crawl skipped some pages for now, so it removed none.
  if (job.stage === "crawled_partial")
    return {
      label: "Partly crawled: some pages could not be read, so none were removed",
      tone: "ready",
    };
  return { label: "Crawl finished", tone: "ready" };
}

export function documentStatus(document: DocumentSummary): StatusView {
  const job = document.latest_job;
  if (isPending(document)) {
    if (job?.error_code) return { label: "Retrying…", tone: "busy" };
    return { label: document.searchable ? "Updating index…" : "Indexing…", tone: "busy" };
  }
  if (job?.status === "failed")
    return { label: `Failed: ${failureReason(job.error_code)}`, tone: "error" };
  if (!document.searchable) return { label: "Not indexed", tone: "idle" };
  const count = document.chunk_count;
  const passages = count === 1 ? "1 passage" : `${count} passages`;
  return { label: `Ready · ${passages}`, tone: "ready" };
}
