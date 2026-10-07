import type { FormEvent, KeyboardEvent } from "react";
import { Icon } from "../../components/Icon";
import { AGENT_NAME } from "./agentName";

interface ComposerProps {
  draft: string;
  change: (value: string) => void;
  submit: () => void;
  disabled: boolean;
  pending: boolean;
  maxLength: number;
}

function shouldSend(event: KeyboardEvent<HTMLTextAreaElement>): boolean {
  return (
    event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing
  );
}

export function Composer(props: ComposerProps) {
  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!props.disabled && props.draft.trim().length > 0) props.submit();
  }
  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (!shouldSend(event)) return;
    event.preventDefault();
    if (!props.disabled && props.draft.trim().length > 0) props.submit();
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
          <ComposerHint pending={props.pending} />
        </span>
        <button
          className="send-button"
          type="submit"
          aria-label="Send message"
          disabled={props.disabled || props.draft.trim().length === 0}
        >
          <Icon name="arrow" />
        </button>
      </div>
    </form>
  );
}

function ComposerHint({ pending }: { pending: boolean }) {
  if (pending) return "The agent is researching your sources…";
  return "Enter to send · Shift + Enter for a new line";
}
