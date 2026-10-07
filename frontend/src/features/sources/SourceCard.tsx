import { useState } from "react";
import type { ChangeEvent } from "react";
import { ApiError } from "../../api/client";
import type { DocumentSummary, Source } from "../../api/contracts";
import { deleteDocument, deleteSource, uploadDocument } from "../../api/knowledge";
import { ErrorNotice } from "../../components/Feedback";
import { Icon } from "../../components/Icon";
import { documentStatus } from "./documentStatus";
import { useAction } from "./useAction";
import { useDocuments } from "./useDocuments";

const ACCEPTED = ".md,.markdown,.txt,text/markdown,text/plain";

interface SourceCardProps {
  source: Source;
  changed: () => void;
  maxUploadBytes: number;
}

async function uploadAll(sourceId: string, files: File[], limit: number) {
  if (files.some((file) => file.size > limit))
    throw new ApiError("payload_too_large", false);
  for (const file of files) await uploadDocument(sourceId, file);
}

export function SourceCard(props: SourceCardProps) {
  const documents = useDocuments(props.source.id, props.changed);
  const settle = () => {
    documents.refresh();
    props.changed();
  };
  const upload = useAction(
    (files: File[]) => uploadAll(props.source.id, files, props.maxUploadBytes),
    settle,
  );
  const remove = useAction(deleteDocument, settle);
  const removeSource = useAction(deleteSource, props.changed);

  function chosen(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (files.length > 0) void upload.run(files);
  }

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
      <label className="upload-button" data-busy={upload.pending}>
        <Icon name="upload" />
        {upload.pending ? "Uploading…" : "Upload files"}
        <input
          type="file"
          accept={ACCEPTED}
          multiple
          disabled={upload.pending}
          aria-label={`Upload files to ${props.source.name}`}
          onChange={chosen}
        />
      </label>
      <ErrorNotice message={upload.error} retry={upload.clear} label="Dismiss" />
      <ErrorNotice message={remove.error} retry={remove.clear} label="Dismiss" />
      <ErrorNotice
        message={removeSource.error}
        retry={removeSource.clear}
        label="Dismiss"
      />
      <ErrorNotice message={documents.error} retry={documents.refresh} />
      <DocumentList items={documents.items} remove={(id) => void remove.run(id)} />
    </li>
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
        <span className="document-title">{props.document.title}</span>
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
