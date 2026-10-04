import { useCallback, useState } from "react";
import type { AgentMetadata, Conversation } from "./api/contracts";
import { ErrorNotice } from "./components/Feedback";
import { Icon } from "./components/Icon";
import { Sidebar } from "./components/Sidebar";
import { useDrawerAccessibility } from "./components/useDrawerAccessibility";
import { AgentStatus, SetupBanner } from "./features/chat/AgentStatus";
import { ChatPanel } from "./features/chat/ChatPanel";
import { readSelection, saveSelection } from "./features/chat/browserState";
import { useConversations } from "./features/chat/useConversations";
import { useCreateConversation } from "./features/chat/useCreateConversation";
import { useMetadata } from "./features/chat/useMetadata";
import { Welcome } from "./features/chat/Welcome";

export function App() {
  const metadata = useMetadata();
  const conversations = useConversations();
  const [selected, setSelected] = useState(readSelection);
  const [initialDraft, setInitialDraft] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const closeSidebar = useCallback(() => setSidebarOpen(false), []);
  useDrawerAccessibility(sidebarOpen, closeSidebar);

  function select(id: string) {
    setSelected(id);
    saveSelection(id);
    setInitialDraft("");
    setSidebarOpen(false);
  }

  function created(conversation: Conversation, draft: string) {
    conversations.insert(conversation);
    select(conversation.id);
    setInitialDraft(draft);
  }

  const creation = useCreateConversation(created);
  const newConversation = () => creation.create("");

  return (
    <div className="app-shell">
      <Sidebar
        items={conversations.items}
        selected={selected}
        loading={conversations.loading}
        error={conversations.error}
        hasMore={conversations.cursor !== null}
        creating={creation.pending}
        open={sidebarOpen}
        select={select}
        create={newConversation}
        more={conversations.more}
        refresh={conversations.refresh}
        close={closeSidebar}
      />
      <SidebarBackdrop open={sidebarOpen} close={closeSidebar} />
      <main className="workspace">
        <header className="workspace-header">
          <button
            className="icon-button mobile-menu"
            aria-label="Conversations"
            aria-expanded={sidebarOpen}
            aria-controls="conversation-sidebar"
            onClick={() => setSidebarOpen(true)}
          >
            <Icon name="menu" />
          </button>
          <div className="agent-heading">
            <h1>Foundation Assistant</h1>
            <p>Provider chat · knowledge retrieval not connected</p>
          </div>
          <AgentStatus agent={metadata.agent} refresh={metadata.refresh} />
        </header>
        <SetupBanner agent={metadata.agent} refresh={metadata.refresh} />
        <div className="workspace-feedback">
          <ErrorNotice message={metadata.error} retry={metadata.refresh} />
          <ErrorNotice message={creation.error} retry={newConversation} />
        </div>
        <ActiveChat
          selected={selected}
          initialDraft={initialDraft}
          agent={metadata.agent}
          create={creation.create}
          creating={creation.pending}
          refresh={conversations.refresh}
        />
      </main>
    </div>
  );
}

interface ActiveChatProps {
  selected: string | null;
  initialDraft: string;
  agent: AgentMetadata | null;
  creating: boolean;
  create: (draft: string) => void;
  refresh: () => void;
}

function ActiveChat(props: ActiveChatProps) {
  if (props.selected === null)
    return (
      <div className="chat-scroll initial-welcome">
        <Welcome suggest={props.create} disabled={props.creating} />
      </div>
    );
  return (
    <ChatPanel
      key={props.selected}
      conversationId={props.selected}
      initialDraft={props.initialDraft}
      agent={props.agent}
      onComplete={props.refresh}
    />
  );
}

function SidebarBackdrop({
  open,
  close,
}: {
  open: boolean;
  close: () => void;
}) {
  if (!open) return null;
  return (
    <button
      className="sidebar-backdrop"
      onClick={close}
      aria-label="Close conversation sidebar"
      tabIndex={-1}
    />
  );
}
