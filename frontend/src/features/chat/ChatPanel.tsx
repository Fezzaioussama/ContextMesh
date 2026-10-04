import type { AgentMetadata, CompletedTurn } from "../../api/contracts";
import { ErrorNotice, Loading } from "../../components/Feedback";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";
import { useHistory } from "./useHistory";
import { useTurn } from "./useTurn";
import { Welcome } from "./Welcome";

interface ChatPanelProps {
  conversationId: string;
  initialDraft: string;
  agent: AgentMetadata | null;
  onComplete: () => void;
}

function canSend(agent: AgentMetadata | null): boolean {
  if (agent === null) return false;
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
  const turn = useTurn(props.conversationId, props.initialDraft, complete);
  const unavailable =
    !canSend(props.agent) || history.loading || history.error.length > 0;
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
        <Composer
          draft={turn.draft}
          change={turn.setDraft}
          submit={turn.submit}
          disabled={disabled}
          pending={turn.pending}
          maxLength={messageLimit(props.agent)}
        />
        <p className="composer-disclaimer">
          AI responses can be mistaken. Check important details.
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
