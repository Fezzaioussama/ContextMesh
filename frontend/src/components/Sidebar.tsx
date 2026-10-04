import type { Conversation } from "../api/contracts";
import { ErrorNotice, Loading } from "./Feedback";
import { Icon } from "./Icon";

interface SidebarProps {
  items: Conversation[];
  selected: string | null;
  loading: boolean;
  error: string;
  hasMore: boolean;
  creating: boolean;
  open: boolean;
  select: (id: string) => void;
  create: () => void;
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
          ContextMesh
          <span className="brand-caption">YOUR CONVERSATION WORKSPACE</span>
        </span>
        <button
          className="icon-button sidebar-close"
          aria-label="Close conversations"
          onClick={props.close}
        >
          <Icon name="close" />
        </button>
      </div>
      <button
        className="new-conversation"
        onClick={props.create}
        disabled={props.creating}
      >
        <Icon name="plus" />
        New conversation
      </button>
      <div className="sidebar-section">
        <span>CONVERSATIONS</span>
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
