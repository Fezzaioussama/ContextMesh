import type { Source } from "../../api/contracts";
import { ErrorNotice, Loading } from "../../components/Feedback";
import { Icon } from "../../components/Icon";
import { CreateSourceForm } from "./CreateSourceForm";
import { SourceCard } from "./SourceCard";
import type { SourcesState } from "./useSources";

interface SourcesPanelProps {
  open: boolean;
  close: () => void;
  sources: SourcesState;
  maxUploadBytes: number;
}

export function SourcesPanel(props: SourcesPanelProps) {
  return (
    <aside
      className="sources-panel"
      data-open={props.open}
      aria-label="Knowledge sources"
      id="sources-panel"
    >
      <div className="sources-header">
        <div>
          <h2>Knowledge sources</h2>
          <p>Answers cite only what is indexed here.</p>
        </div>
        <button
          className="icon-button"
          aria-label="Close sources"
          onClick={props.close}
        >
          <Icon name="close" />
        </button>
      </div>
      <CreateSourceForm created={props.sources.refresh} />
      <ErrorNotice message={props.sources.error} retry={props.sources.refresh} />
      <SourceList
        items={props.sources.items}
        loading={props.sources.loading}
        changed={props.sources.refresh}
        maxUploadBytes={props.maxUploadBytes}
      />
    </aside>
  );
}

interface SourceListProps {
  items: Source[];
  loading: boolean;
  changed: () => void;
  maxUploadBytes: number;
}

function SourceList(props: SourceListProps) {
  if (props.loading) return <Loading>Loading sources…</Loading>;
  if (props.items.length === 0)
    return (
      <p className="sources-empty">
        Create a source, then upload Markdown or text files. Indexed passages
        become searchable evidence.
      </p>
    );
  return (
    <ul className="source-list">
      {props.items.map((source) => (
        <SourceCard
          key={source.id}
          source={source}
          changed={props.changed}
          maxUploadBytes={props.maxUploadBytes}
        />
      ))}
    </ul>
  );
}
