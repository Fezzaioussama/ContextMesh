import { useEffect, useRef } from "react";
import type { Message } from "../../api/contracts";
import { Icon } from "../../components/Icon";
import { Loading } from "../../components/Feedback";

function author(role: Message["role"]): string {
  if (role === "user") return "You";
  return "Foundation Assistant";
}

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
  pending: boolean;
}) {
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (end.current !== null)
      end.current.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [messages, pending]);
  return (
    <section className="message-list" aria-label="Conversation messages">
      <div role="log" aria-live="polite" aria-relevant="additions">
        {messages.map((message) => (
          <article
            key={message.id}
            className="message"
            data-role={message.role}
          >
            <div className="message-avatar">
              <MessageAvatar role={message.role} />
            </div>
            <div className="message-body">
              <div className="message-meta">
                <strong>{author(message.role)}</strong>
                <time dateTime={message.created_at}>
                  {timestamp(message.created_at)}
                </time>
              </div>
              <div className="message-content">{message.content}</div>
            </div>
          </article>
        ))}
      </div>
      <PendingMessage pending={pending} />
      <div ref={end} />
    </section>
  );
}

function MessageAvatar({ role }: { role: Message["role"] }) {
  if (role === "user") return <span>Y</span>;
  return <Icon name="mesh" />;
}

function PendingMessage({ pending }: { pending: boolean }) {
  if (!pending) return null;
  return <Loading>Foundation Assistant is responding…</Loading>;
}
