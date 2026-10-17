import { useEffect, useRef, useState } from "react";
import { describeError, evidence } from "../../api/client";
import type {
  Answer,
  AnswerStatus,
  Citation,
  Claim,
  Message,
  TraceStage,
} from "../../api/contracts";
import { ExternalLink, Icon } from "../../components/ui";
import { Markdown } from "../../components/Markdown";
import { AGENT_NAME, type PendingTurn } from "./hooks";

function timestamp(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function MessageList({
  messages,
  pending,
}: {
  messages: Message[];
  pending: PendingTurn | null;
}) {
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [messages, pending]);
  return (
    <section className="message-list" aria-label="Conversation messages">
      <div role="log" aria-live="polite" aria-relevant="additions">
        {messages.map((message) => (
          <SavedMessage key={message.id} message={message} />
        ))}
      </div>
      {pending !== null && <StreamingMessages turn={pending} />}
      <div ref={end} />
    </section>
  );
}

/** One saved message with its author, time, and content. */
function SavedMessage({ message }: { message: Message }) {
  const fromUser = message.role === "user";
  return (
    <article className="message" data-role={message.role}>
      <div className="message-avatar">{fromUser ? <span>Y</span> : <Icon name="mesh" />}</div>
      <div className="message-body">
        <div className="message-meta">
          <strong>{fromUser ? "You" : AGENT_NAME}</strong>
          <time dateTime={message.created_at}>{timestamp(message.created_at)}</time>
        </div>
        <MessageContent message={message} />
      </div>
    </article>
  );
}

function StreamingMessages({ turn }: { turn: PendingTurn }) {
  return (
    <div aria-busy="true">
      <article className="message" data-role="user">
        <div className="message-avatar">
          <span>Y</span>
        </div>
        <div className="message-body">
          <div className="message-meta">
            <strong>You</strong>
            <span>Sending</span>
          </div>
          <div className="message-content">{turn.message}</div>
        </div>
      </article>
      <article className="message" data-role="assistant" aria-label="Answer in progress">
        <div className="message-avatar">
          <Icon name="mesh" />
        </div>
        <div className="message-body">
          <div className="message-meta">
            <strong>{AGENT_NAME}</strong>
            <span>Writing</span>
          </div>
          <div className="stream-progress" role="status">{turn.progress}</div>
          {turn.text.length > 0 && (
            <div className="stream-draft">
              <Markdown>{turn.text}</Markdown>
            </div>
          )}
        </div>
      </article>
    </div>
  );
}

function MessageContent({ message }: { message: Message }) {
  if (message.answer === null) {
    if (message.role === "assistant") return <Markdown>{message.content}</Markdown>;
    return <div className="message-content">{message.content}</div>;
  }
  return (
    <AnswerView
      messageId={message.id}
      answer={message.answer}
      content={message.content}
      trace={message.trace}
    />
  );
}

// Answers: claims with citation markers, gaps, citations, and the agent's trace

const statusLabels: Record<AnswerStatus, string> = {
  direct: "Direct answer",
  answered: "Grounded answer",
  partial: "Partial answer",
  insufficient_evidence: "Not enough evidence",
  withheld: "Answer withheld",
};

const stageLabels: Record<string, string> = {
  route: "Route",
  direct: "Answer",
  plan: "Plan",
  retrieve: "Search",
  rerank: "Rerank",
  assess: "Assess",
  expand: "Expand",
  answer: "Draft",
  verify: "Verify",
  repair: "Repair",
  release: "Release",
};

interface AnswerViewProps {
  messageId: string;
  answer: Answer;
  content: string;
  trace: TraceStage[];
}

function AnswerView(props: AnswerViewProps) {
  const anchor = (citation: string) => `${props.messageId}-${citation}`;
  return (
    <div className="answer" data-status={props.answer.status}>
      <span className="answer-status">{statusLabels[props.answer.status]}</span>
      <ClaimText answer={props.answer} fallback={props.content} anchor={anchor} />
      {props.answer.gaps.length > 0 && (
        <div className="answer-gaps">
          <strong>Not found in your sources</strong>
          <ul>
            {props.answer.gaps.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
        </div>
      )}
      <CitationList citations={props.answer.citations} anchor={anchor} />
      <TraceDetails trace={props.trace} />
    </div>
  );
}

function ClaimText(props: {
  answer: Answer;
  fallback: string;
  anchor: (citation: string) => string;
}) {
  if (props.answer.claims.length === 0)
    return <Markdown>{props.fallback}</Markdown>;
  const numbers = new Map(props.answer.citations.map((item) => [item.id, item.number]));
  return (
    <div className="answer-claims">
      {props.answer.claims.map((claim) => (
        <ClaimSentence key={claim.id} claim={claim} numbers={numbers} anchor={props.anchor} />
      ))}
    </div>
  );
}

function ClaimSentence(props: {
  claim: Claim;
  numbers: Map<string, number>;
  anchor: (citation: string) => string;
}) {
  return (
    <div className="claim">
      <Markdown>{props.claim.text}</Markdown>
      <span className="claim-citations">
        {props.claim.citation_ids.filter((id) => props.numbers.has(id)).map((id) => (
          <a
            key={id}
            className="citation-marker"
            href={`#${props.anchor(id)}`}
            aria-label={`Citation ${props.numbers.get(id)}`}
          >
            {props.numbers.get(id)}
          </a>
        ))}
      </span>
    </div>
  );
}

function CitationList(props: {
  citations: Citation[];
  anchor: (citation: string) => string;
}) {
  if (props.citations.length === 0) return null;
  return (
    <ol className="citation-list" aria-label="Citations">
      {props.citations.map((citation) => (
        <CitationItem key={citation.id} citation={citation} id={props.anchor(citation.id)} />
      ))}
    </ol>
  );
}

/** Pages and slides for documents; source lines only where they help (text files). */
function position(citation: Citation): string {
  const { locator } = citation;
  if (locator.page !== null) return `page ${locator.page}`;
  if (locator.slide !== null) return `slide ${locator.slide}`;
  if (citation.source_url !== null) return "";
  return `lines ${locator.line_start}–${locator.line_end}`;
}

function location(citation: Citation): string {
  const parts = [citation.locator.heading_path.join(" › "), position(citation)];
  return parts.filter((part) => part.length > 0).join(" · ");
}

function CitationItem({ citation, id }: { citation: Citation; id: string }) {
  return (
    <li className="citation" id={id}>
      <span className="citation-number">{citation.number}</span>
      <div>
        <strong>{citation.title}</strong>
        <span className="citation-location">{location(citation)}</span>
        <blockquote>{citation.snippet}</blockquote>
        <ExternalLink href={citation.source_url}>Open page ↗</ExternalLink>
        <PassageLoader path={citation.evidence_path} />
      </div>
    </li>
  );
}

function PassageLoader({ path }: { path: string }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState("");

  async function load() {
    setError("");
    try {
      setText((await evidence(path, new AbortController().signal)).text);
    } catch (failure) {
      setError(describeError(failure));
    }
  }

  if (text !== null) return <pre className="evidence-text">{text}</pre>;
  return (
    <>
      <button className="text-button" onClick={() => void load()}>
        Show full passage
      </button>
      {error.length > 0 && (
        <span className="evidence-error" role="alert">
          {error}
        </span>
      )}
    </>
  );
}

function TraceDetails({ trace }: { trace: TraceStage[] }) {
  if (trace.length === 0) return null;
  return (
    <details className="answer-trace">
      <summary>How this answer was found · {trace.length} steps</summary>
      <ol>
        {trace.map((stage, index) => (
          <li key={`${stage.stage}-${index}`}>
            <strong>{stageLabels[stage.stage] ?? stage.stage}</strong>
            {stage.summary}
          </li>
        ))}
      </ol>
    </details>
  );
}
