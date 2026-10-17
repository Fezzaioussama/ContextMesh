import { useState } from "react";
import type { Conversation } from "../api/contracts";
import { ErrorNotice, Icon, Loading } from "./ui";

interface SidebarProps {
  items: Conversation[];
  selected: string | null;
  loading: boolean;
  error: string;
  hasMore: boolean;
  creating: boolean;
  /** How many sources the open conversation is narrowed to; null when it searches all. */
  narrowed: number | null;
  open: boolean;
  select: (id: string) => void;
  create: (keepScope: boolean) => void;
  more: () => void;
  refresh: () => void;
  close: () => void;
}

export function Sidebar(props: SidebarProps) {
  return (
    <aside
      className="sidebar"
      data-open={props.open}
      aria-label="Conversation sidebar"
      id="conversation-sidebar"
    >
      <div className="brand">
        <span className="brand-symbol">
          <Icon name="mesh" />
        </span>
        <span>
          Context<span className="brand-word-accent">Mesh</span>
          <span className="brand-caption">KNOWLEDGE, CONNECTED.</span>
        </span>
        <button
          className="icon-button sidebar-close"
          aria-label="Close conversations"
          onClick={props.close}
        >
          <Icon name="close" />
        </button>
      </div>
      <NewConversation
        narrowed={props.narrowed}
        creating={props.creating}
        create={props.create}
      />
      <div className="sidebar-section">
        <span>YOUR CONVERSATIONS</span>
        <button
          className="icon-button"
          aria-label="Refresh conversations"
          onClick={props.refresh}
          disabled={props.loading}
        >
          <Icon name="refresh" />
        </button>
      </div>
      <ConversationList
        items={props.items}
        selected={props.selected}
        select={props.select}
      />
      <SidebarStatus loading={props.loading} empty={props.items.length === 0} />
      <ErrorNotice message={props.error} retry={props.refresh} />
      <MoreConversations
        visible={props.hasMore}
        loading={props.loading}
        more={props.more}
      />
      <div className="sidebar-footer">
        <span className="workspace-avatar">L</span>
        <div>
          Local workspace<span>Development identity</span>
        </div>
        <span className="local-badge">LOCAL</span>
      </div>
    </aside>
  );
}

/** With a narrowed source selection, asks whether the new conversation keeps it. */
function NewConversation({
  narrowed,
  creating,
  create,
}: Pick<SidebarProps, "narrowed" | "creating" | "create">) {
  const [choosing, setChoosing] = useState(false);
  const asking = choosing && narrowed !== null;
  function start() {
    if (narrowed === null) create(false);
    else setChoosing((value) => !value);
  }
  function choose(keepScope: boolean) {
    setChoosing(false);
    create(keepScope);
  }
  return (
    <div className="new-conversation-area">
      <button
        className="new-conversation"
        onClick={start}
        disabled={creating}
        aria-expanded={narrowed === null ? undefined : asking}
        aria-controls={narrowed === null ? undefined : "new-conversation-choice"}
      >
        <Icon name="plus" />
        New conversation
      </button>
      {asking && (
        <div
          className="new-conversation-choice"
          id="new-conversation-choice"
          role="group"
          aria-label="Sources for the new conversation"
        >
          <p>Sources for the new conversation</p>
          <button onClick={() => choose(true)}>
            Keep the {narrowed} selected {narrowed === 1 ? "source" : "sources"}
          </button>
          <button onClick={() => choose(false)}>Search all sources</button>
        </div>
      )}
    </div>
  );
}

function ConversationList({
  items,
  selected,
  select,
}: Pick<SidebarProps, "items" | "selected" | "select">) {
  return (
    <nav className="conversation-list" aria-label="Saved conversations">
      {items.map((item) => (
        <button
          key={item.id}
          className="conversation-item"
          data-conversation-id={item.id}
          aria-current={item.id === selected}
          aria-label={`Open conversation: ${item.title}`}
          onClick={() => select(item.id)}
        >
          <Icon name="chat" />
          <span>{item.title}</span>
        </button>
      ))}
    </nav>
  );
}

function SidebarStatus({
  loading,
  empty,
}: {
  loading: boolean;
  empty: boolean;
}) {
  if (loading) return <Loading>Loading conversations…</Loading>;
  if (empty)
    return (
      <p className="sidebar-empty">
        Your conversations will appear here.
        <br />
        Start with something on your mind.
      </p>
    );
  return null;
}

function MoreConversations({
  visible,
  loading,
  more,
}: {
  visible: boolean;
  loading: boolean;
  more: () => void;
}) {
  if (!visible) return null;
  return (
    <button className="text-button load-more" disabled={loading} onClick={more}>
      Load more conversations
    </button>
  );
}
