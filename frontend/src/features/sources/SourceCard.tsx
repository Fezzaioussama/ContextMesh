import { useCallback, useState } from "react";
import type { ChangeEvent } from "react";
import { ApiError } from "../../api/client";
import type { DocumentSummary, Source } from "../../api/contracts";
import {
  deleteDocument,
  deleteSource,
  syncSource,
  uploadDocument,
} from "../../api/knowledge";
import { ExternalLink, webUrl } from "../../components/ExternalLink";
import { ErrorNotice } from "../../components/Feedback";
import { Icon } from "../../components/Icon";
import { crawlStatus, documentStatus, isOpen } from "./documentStatus";
import { useAction } from "./useAction";
import { useDocuments } from "./useDocuments";
import { usePolling } from "./usePolling";

interface SourceCardProps {
  source: Source;
  changed: () => void;
  maxUploadBytes: number;
  accept: string;
}

async function uploadAll(sourceId: string, files: File[], limit: number) {
  if (files.some((file) => file.size > limit))
    throw new ApiError("payload_too_large", false);
  for (const file of files) await uploadDocument(sourceId, file);
}

/** Polls while a crawl runs; each poll re-arms the next one until the crawl ends. */
function useCrawlWatch(active: boolean, refresh: () => void) {
  const [tick, setTick] = useState(0);
  const poll = useCallback(() => {
    refresh();
    setTick((value) => value + 1);
  }, [refresh]);
  usePolling(active, tick, poll);
}

export function SourceCard(props: SourceCardProps) {
  const { changed } = props;
  const documents = useDocuments(props.source.id, changed);
  const { refresh } = documents;
  const settle = useCallback(() => {
    refresh();
    changed();
  }, [refresh, changed]);
  useCrawlWatch(isOpen(props.source.latest_sync), settle);
  const remove = useAction(deleteDocument, settle);
  const removeSource = useAction(deleteSource, changed);

  return (
    <li className="source-card">
      <div className="source-title">
        <Icon name="library" />
        <div>
          <strong>{props.source.name}</strong>
          <SourceDescription text={props.source.description} />
        </div>
        <DeleteSource
          name={props.source.name}
          pending={removeSource.pending}
          confirm={() => void removeSource.run(props.source.id)}
        />
      </div>
      <SourceControls {...props} settle={settle} />
      <ErrorNotice message={remove.error} retry={remove.clear} label="Dismiss" />
      <ErrorNotice message={removeSource.error} retry={removeSource.clear} label="Dismiss" />
      <ErrorNotice message={documents.error} retry={documents.refresh} />
      <DocumentList items={documents.items} remove={(id) => void remove.run(id)} />
    </li>
  );
}

function SourceControls(props: SourceCardProps & { settle: () => void }) {
  if (props.source.kind === "website")
    return <WebsiteControls source={props.source} settle={props.settle} />;
  return <UploadControls {...props} />;
}

function UploadControls(props: SourceCardProps & { settle: () => void }) {
  const upload = useAction(
    (files: File[]) => uploadAll(props.source.id, files, props.maxUploadBytes),
    props.settle,
  );

  function chosen(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (files.length > 0) void upload.run(files);
  }

  return (
    <>
      <label className="upload-button" data-busy={upload.pending}>
        <Icon name="upload" />
        {upload.pending ? "Uploading…" : "Upload files"}
        <input
          type="file"
          accept={props.accept}
          multiple
          disabled={upload.pending}
          aria-label={`Upload files to ${props.source.name}`}
          onChange={chosen}
        />
      </label>
      <ErrorNotice message={upload.error} retry={upload.clear} label="Dismiss" />
    </>
  );
}

function WebsiteControls(props: { source: Source; settle: () => void }) {
  const crawl = useAction(syncSource, props.settle);
  const status = crawlStatus(props.source.latest_sync);
  const crawling = crawl.pending || isOpen(props.source.latest_sync);
  return (
    <div className="website-controls">
      <ExternalLink href={props.source.url}>{props.source.url}</ExternalLink>
      <span className="document-status" data-tone={status.tone} role="status">
        {status.label}
      </span>
      <button
        className="text-button"
        disabled={crawling}
        onClick={() => void crawl.run(props.source.id)}
      >
        Crawl again
      </button>
      <ErrorNotice message={crawl.error} retry={crawl.clear} label="Dismiss" />
    </div>
  );
}

function SourceDescription({ text }: { text: string }) {
  if (text.length === 0) return null;
  return <p>{text}</p>;
}

function DeleteSource(props: {
  name: string;
  pending: boolean;
  confirm: () => void;
}) {
  const [asking, setAsking] = useState(false);
  if (!asking)
    return (
      <button
        className="icon-button"
        aria-label={`Delete source ${props.name}`}
        onClick={() => setAsking(true)}
      >
        <Icon name="trash" />
      </button>
    );
  return (
    <span className="confirm-delete">
      <button className="text-button danger" onClick={props.confirm} disabled={props.pending}>
        Delete
      </button>
      <button className="text-button" onClick={() => setAsking(false)}>
        Keep
      </button>
    </span>
  );
}

function DocumentList(props: {
  items: DocumentSummary[];
  remove: (id: string) => void;
}) {
  if (props.items.length === 0)
    return <p className="document-empty">No documents yet.</p>;
  return (
    <ul className="document-list" aria-label="Documents">
      {props.items.map((document) => (
        <DocumentRow key={document.id} document={document} remove={props.remove} />
      ))}
    </ul>
  );
}

function DocumentRow(props: {
  document: DocumentSummary;
  remove: (id: string) => void;
}) {
  const status = documentStatus(props.document);
  return (
    <li className="document-row">
      <Icon name="file" />
      <div>
        <DocumentTitle document={props.document} />
        <span className="document-status" data-tone={status.tone} role="status">
          {status.label}
        </span>
      </div>
      <button
        className="icon-button"
        aria-label={`Delete ${props.document.title}`}
        onClick={() => props.remove(props.document.id)}
      >
        <Icon name="trash" />
      </button>
    </li>
  );
}

function DocumentTitle({ document }: { document: DocumentSummary }) {
  if (webUrl(document.uri) === null)
    return <span className="document-title">{document.title}</span>;
  return (
    <span className="document-title">
      <ExternalLink href={document.uri}>{document.title}</ExternalLink>
    </span>
  );
}
