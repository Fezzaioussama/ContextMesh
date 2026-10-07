import { requestJson } from "./client";
import type {
  DocumentSummary,
  Evidence,
  Source,
  UploadReceipt,
} from "./contracts";

const base = "/api/v1";

export async function sources(signal: AbortSignal): Promise<Source[]> {
  const page = await requestJson<{ items: Source[] }>(`${base}/sources`, {
    signal,
  });
  return page.items;
}

export interface SourceRequest {
  name: string;
  description: string;
  url: string | null;
}

/** A URL registers a website to crawl; without one the source holds uploaded files. */
export function createSource(request: SourceRequest): Promise<Source> {
  return requestJson(`${base}/sources`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export function syncSource(id: string): Promise<{ job_id: string }> {
  return requestJson(`${base}/sources/${encodeURIComponent(id)}/sync`, {
    method: "POST",
  });
}

export function deleteSource(id: string): Promise<{ job_id: string }> {
  return requestJson(`${base}/sources/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function sourceDocuments(
  id: string,
  signal: AbortSignal,
): Promise<DocumentSummary[]> {
  const path = `${base}/sources/${encodeURIComponent(id)}/documents`;
  const page = await requestJson<{ items: DocumentSummary[] }>(path, {
    signal,
  });
  return page.items;
}

export function uploadDocument(
  sourceId: string,
  file: File,
): Promise<UploadReceipt> {
  const form = new FormData();
  form.append("file", file);
  const path = `${base}/sources/${encodeURIComponent(sourceId)}/documents`;
  return requestJson(path, { method: "POST", body: form });
}

export function deleteDocument(id: string): Promise<{ job_id: string }> {
  return requestJson(`${base}/documents/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export function evidence(path: string, signal: AbortSignal): Promise<Evidence> {
  if (!path.startsWith(`${base}/documents/`))
    return Promise.reject(new Error("Unexpected evidence path."));
  return requestJson(path, { signal });
}
