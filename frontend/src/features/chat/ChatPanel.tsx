import type { AgentMetadata, CompletedTurn, Source } from "../../api/contracts";
import { ErrorNotice, Loading } from "../../components/Feedback";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";
import { ScopePicker } from "./ScopePicker";
import type { SearchScope } from "./ScopePicker";
import { useHistory } from "./useHistory";
import { useTurn } from "./useTurn";
import { Welcome } from "./Welcome";

interface ChatPanelProps {
  conversationId: string;
  initialDraft: string;
  agent: AgentMetadata | null;
  sources: Source[];
  scope: SearchScope;
  changeScope: (scope: string[] | null) => void;
  onComplete: () => void;
}

function canSend(agent: AgentMetadata | null, scope: SearchScope): boolean {
  if (agent === null || scope.unavailable) return false;
  return agent.configured;
}

function messageLimit(agent: AgentMetadata | null): number {
  if (agent === null) return 8000;
  return agent.limits.max_message_chars;
}

export function ChatPanel(props: ChatPanelProps) {
  const history = useHistory(props.conversationId);
  function complete(turn: CompletedTurn) {
    history.complete(turn);
    props.onComplete();
  }
  const turn = useTurn(
    props.conversationId,
    props.initialDraft,
    props.scope.ids,
    complete,
  );
  const unavailable =
    !canSend(props.agent, props.scope) ||
    history.loading ||
    history.error.length > 0;
  const disabled = unavailable || turn.pending;

  return (
    <>
      <div className="chat-scroll">
        <HistoryContent
          history={history}
          pending={turn.pending}
          suggest={turn.setDraft}
          disabled={disabled}
        />
        <ErrorNotice message={history.error} retry={history.refresh} />
      </div>
      <div className="composer-area">
        <ErrorNotice
          message={turn.error}
          retry={turn.submit}
          label="Retry message"
        />
        <ScopePicker
          sources={props.sources}
          scope={props.scope}
          change={props.changeScope}
        />
        <Composer
          draft={turn.draft}
          change={turn.setDraft}
          submit={turn.submit}
          disabled={disabled}
          pending={turn.pending}
          maxLength={messageLimit(props.agent)}
        />
        <p className="composer-disclaimer">
          Answers cite indexed passages. Check important details in the cited
          source.
        </p>
      </div>
    </>
  );
}

interface HistoryContentProps {
  history: ReturnType<typeof useHistory>;
  pending: boolean;
  suggest: (message: string) => void;
  disabled: boolean;
}

function HistoryContent({
  history,
  pending,
  suggest,
  disabled,
}: HistoryContentProps) {
  if (history.loading) return <Loading>Loading conversation…</Loading>;
  if (history.error.length > 0) return null;
  if (history.messages.length === 0)
    return <Welcome suggest={suggest} disabled={disabled} />;
  return <MessageList messages={history.messages} pending={pending} />;
}
