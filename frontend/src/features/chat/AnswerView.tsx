import { useState } from "react";
import type {
  Answer,
  AnswerStatus,
  Citation,
  Claim,
  TraceStage,
} from "../../api/contracts";
import { describeError } from "../../api/client";
import { evidence } from "../../api/knowledge";
import { ExternalLink } from "../../components/ExternalLink";

const statusLabels: Record<AnswerStatus, string> = {
  answered: "Grounded answer",
  partial: "Partial answer",
  insufficient_evidence: "Not enough evidence",
  withheld: "Answer withheld",
};

const stageLabels: Record<string, string> = {
  plan: "Plan",
  retrieve: "Search",
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

export function AnswerView(props: AnswerViewProps) {
  const anchor = (citation: string) => `${props.messageId}-${citation}`;
  return (
    <div className="answer" data-status={props.answer.status}>
      <span className="answer-status">{statusLabels[props.answer.status]}</span>
      <ClaimText answer={props.answer} fallback={props.content} anchor={anchor} />
      <GapList gaps={props.answer.gaps} />
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
    return <p className="message-content">{props.fallback}</p>;
  const numbers = new Map(props.answer.citations.map((item) => [item.id, item.number]));
  return (
    <p className="message-content">
      {props.answer.claims.map((claim) => (
        <ClaimSentence key={claim.id} claim={claim} numbers={numbers} anchor={props.anchor} />
      ))}
    </p>
  );
}

function ClaimSentence(props: {
  claim: Claim;
  numbers: Map<string, number>;
  anchor: (citation: string) => string;
}) {
  return (
    <span className="claim">
      {props.claim.text}
      {props.claim.citation_ids.map((id) => (
        <a
          key={id}
          className="citation-marker"
          href={`#${props.anchor(id)}`}
          aria-label={`Citation ${props.numbers.get(id)}`}
        >
          {props.numbers.get(id)}
        </a>
      ))}{" "}
    </span>
  );
}

function GapList({ gaps }: { gaps: string[] }) {
  if (gaps.length === 0) return null;
  return (
    <div className="answer-gaps">
      <strong>Not found in your sources</strong>
      <ul>
        {gaps.map((gap) => (
          <li key={gap}>{gap}</li>
        ))}
      </ul>
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
      <ErrorText message={error} />
    </>
  );
}

function ErrorText({ message }: { message: string }) {
  if (message.length === 0) return null;
  return (
    <span className="evidence-error" role="alert">
      {message}
    </span>
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
