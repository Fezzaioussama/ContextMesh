import type { FormEvent, KeyboardEvent } from "react";
import type { AgentMetadata, CompletedTurn, Source } from "../../api/contracts";
import { ErrorNotice, Icon, Loading } from "../../components/ui";
import { AGENT_NAME, useHistory, useTurn, type PendingTurn } from "./hooks";
import { MessageList } from "./Messages";

export interface SearchScope {
  ids: string[] | null;
  unavailable: boolean;
}

interface ChatPanelProps {
  conversationId: string;
  initialDraft: string;
  agent: AgentMetadata | null;
  sources: Source[];
  scope: SearchScope;
  changeScope: (scope: string[] | null) => void;
  onComplete: () => void;
}

export function ChatPanel(props: ChatPanelProps) {
  const history = useHistory(props.conversationId);
  function complete(turn: CompletedTurn) {
    history.complete(turn);
    props.onComplete();
  }
  const turn = useTurn(props.conversationId, props.initialDraft, props.scope.ids, complete);
  const canSend = props.agent !== null && props.agent.configured && !props.scope.unavailable;
  const disabled =
    !canSend || history.loading || history.error.length > 0 || turn.pending;

  return (
    <>
      <div className="chat-scroll">
        <HistoryContent
          history={history}
          pending={turn.stream}
          suggest={turn.setDraft}
          disabled={disabled}
        />
        <ErrorNotice message={history.error} retry={history.refresh} />
      </div>
      <div className="composer-area">
        <ErrorNotice message={turn.error} retry={turn.retry} label="Retry message" />
        <ScopePicker sources={props.sources} scope={props.scope} change={props.changeScope} />
        <Composer
          draft={turn.draft}
          change={turn.setDraft}
          submit={turn.submit}
          disabled={disabled}
          pending={turn.pending}
          maxLength={props.agent?.limits.max_message_chars ?? 8000}
        />
        <p className="composer-disclaimer">
          Answers cite indexed passages. Check important details in the cited
          source.
        </p>
      </div>
    </>
  );
}

function HistoryContent(props: {
  history: ReturnType<typeof useHistory>;
  pending: PendingTurn | null;
  suggest: (message: string) => void;
  disabled: boolean;
}) {
  const { history } = props;
  if (history.loading) return <Loading>Loading conversation…</Loading>;
  if (history.error.length > 0) return null;
  if (history.messages.length === 0 && props.pending === null)
    return <Welcome suggest={props.suggest} disabled={props.disabled} />;
  return <MessageList messages={history.messages} pending={props.pending} />;
}

interface ComposerProps {
  draft: string;
  change: (value: string) => void;
  submit: () => void;
  disabled: boolean;
  pending: boolean;
  maxLength: number;
}

function Composer(props: ComposerProps) {
  const empty = props.draft.trim().length === 0;
  function send() {
    if (!props.disabled && !empty) props.submit();
  }
  function onSubmit(event: FormEvent) {
    event.preventDefault();
    send();
  }
  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends; Shift+Enter and IME composition keep typing.
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    send();
  }
  return (
    <form className="composer" onSubmit={onSubmit}>
      <label className="sr-only" htmlFor="message">
        Message {AGENT_NAME}
      </label>
      <textarea
        id="message"
        value={props.draft}
        onChange={(event) => props.change(event.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Ask a question about your sources…"
        maxLength={props.maxLength}
        disabled={props.disabled}
        rows={2}
      />
      <div className="composer-bottom">
        <span>
          {props.pending
            ? "Writing your answer…"
            : "Enter to send · Shift + Enter for a new line"}
        </span>
        <button
          className="send-button"
          type="submit"
          aria-label="Send message"
          disabled={props.disabled || empty}
        >
          <Icon name="arrow" />
        </button>
      </div>
    </form>
  );
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

/** Choosing a source while "all" is active narrows the search to that source. */
function toggledScope(scope: string[] | null, id: string, sources: Source[]): string[] | null {
  if (scope === null) return [id];
  const next = scope.includes(id) ? scope.filter((item) => item !== id) : [...scope, id];
  return next.length === 0 || next.length === sources.length ? null : next;
}

interface ScopePickerProps {
  sources: Source[];
  scope: SearchScope;
  change: (scope: string[] | null) => void;
}

function ScopePicker({ sources, scope, change }: ScopePickerProps) {
  if (sources.length === 0 && !scope.unavailable)
    return <span className="scope-summary">No indexed sources yet</span>;
  const summary = scope.unavailable
    ? "Selected sources are no longer searchable"
    : scope.ids === null
      ? `Searching all ${sources.length} sources`
      : `Searching ${scope.ids.length} of ${sources.length} sources`;
  return (
    <details className="scope-picker" data-unavailable={scope.unavailable}>
      <summary>{summary}</summary>
      <fieldset>
        <legend>Sources to search</legend>
        <label>
          <input type="checkbox" checked={scope.ids === null} onChange={() => change(null)} />
          All sources
        </label>
        {sources.map((source) => (
          <label key={source.id}>
            <input
              type="checkbox"
              checked={scope.ids?.includes(source.id) ?? false}
              onChange={() => change(toggledScope(scope.ids, source.id, sources))}
            />
            {source.name}
          </label>
        ))}
      </fieldset>
    </details>
  );
}

const suggestions = [
  {
    title: "Recall a decision",
    text: "What did we decide about authentication, and why?",
    icon: "01",
  },
  {
    title: "Find a detail",
    text: "When does the rollout start, and which service goes first?",
    icon: "02",
  },
  {
    title: "Check for conflicts",
    text: "Do any documents disagree about the release plan?",
    icon: "03",
  },
];

export function Welcome({
  suggest,
  disabled,
}: {
  suggest: (message: string) => void;
  disabled: boolean;
}) {
  return (
    <section className="welcome" aria-label="Start chatting">
      <div className="welcome-symbol">
        <Icon name="mesh" />
      </div>
      <p className="eyebrow">GROUNDED IN YOUR SOURCES</p>
      <h2>What do you want to find out?</h2>
      <p className="welcome-description">
        The agent plans a search, expands it when evidence is missing, and
        cites the passages behind every claim.
      </p>
      <div className="suggestion-grid">
        {suggestions.map((suggestion) => (
          <button
            className="suggestion"
            key={suggestion.title}
            onClick={() => suggest(suggestion.text)}
            disabled={disabled}
          >
            <span className="suggestion-number">{suggestion.icon}</span>
            <strong>{suggestion.title}</strong>
            <span>{suggestion.text}</span>
            <span className="suggestion-arrow">↗</span>
          </button>
        ))}
      </div>
      <p className="welcome-note">
        Agentic retrieval · answers without evidence are reported as gaps
      </p>
    </section>
  );
}
