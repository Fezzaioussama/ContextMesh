import type { Source } from "../../api/contracts";

export interface SearchScope {
  ids: string[] | null;
  unavailable: boolean;
}

interface ScopePickerProps {
  sources: Source[];
  scope: SearchScope;
  change: (scope: string[] | null) => void;
}

/**
 * A null scope searches every eligible source. An explicit selection is a hard
 * bound: removed sources narrow it, and it never silently widens to "all".
 */
export function effectiveScope(scope: string[] | null, sources: Source[]): SearchScope {
  if (scope === null) return { ids: null, unavailable: false };
  const known = scope.filter((id) => sources.some((source) => source.id === id));
  return { ids: known, unavailable: known.length === 0 };
}

function flipped(current: string[], id: string): string[] {
  return current.includes(id)
    ? current.filter((item) => item !== id)
    : [...current, id];
}

/** Choosing a source while "all" is active narrows the search to that source. */
export function toggledScope(
  scope: string[] | null,
  id: string,
  sources: Source[],
): string[] | null {
  if (scope === null) return [id];
  const next = flipped(scope, id);
  return next.length === 0 || next.length === sources.length ? null : next;
}

function summary(scope: SearchScope, total: number): string {
  if (scope.unavailable) return "Selected sources are no longer searchable";
  if (scope.ids === null) return `Searching all ${total} sources`;
  return `Searching ${scope.ids.length} of ${total} sources`;
}

export function ScopePicker(props: ScopePickerProps) {
  if (props.sources.length === 0 && !props.scope.unavailable)
    return <span className="scope-summary">No indexed sources yet</span>;
  return (
    <details className="scope-picker" data-unavailable={props.scope.unavailable}>
      <summary>{summary(props.scope, props.sources.length)}</summary>
      <fieldset>
        <legend>Sources to search</legend>
        <label>
          <input
            type="checkbox"
            checked={props.scope.ids === null}
            onChange={() => props.change(null)}
          />
          All sources
        </label>
        {props.sources.map((source) => (
          <SourceOption key={source.id} source={source} {...props} />
        ))}
      </fieldset>
    </details>
  );
}

function SourceOption(props: ScopePickerProps & { source: Source }) {
  const ids = props.scope.ids;
  const selected = ids !== null && ids.includes(props.source.id);
  return (
    <label>
      <input
        type="checkbox"
        checked={selected}
        onChange={() => props.change(toggledScope(ids, props.source.id, props.sources))}
      />
      {props.source.name}
    </label>
  );
}
