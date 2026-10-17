import { useCallback, useEffect, useState } from "react";
import type { AgentMetadata, Conversation, Source } from "./api/contracts";
import { Sidebar } from "./components/Sidebar";
import { ErrorNotice, Icon } from "./components/ui";
import { AgentStatus, SetupBanner } from "./features/chat/AgentStatus";
import { ChatPanel, effectiveScope, Welcome } from "./features/chat/ChatPanel";
import type { SearchScope } from "./features/chat/ChatPanel";
import {
  AGENT_NAME,
  readScope,
  readSelection,
  saveScope,
  saveSelection,
  useConversations,
  useCreateConversation,
  useMetadata,
} from "./features/chat/hooks";
import { useSources } from "./features/sources/hooks";
import type { SourcesState } from "./features/sources/hooks";
import { SourcesPanel } from "./features/sources/SourcesPanel";

const DEFAULT_UPLOAD_LIMIT = 20_000_000;

type Metadata = ReturnType<typeof useMetadata>;
type Conversations = ReturnType<typeof useConversations>;
type Creation = ReturnType<typeof useCreateConversation>;

export function App() {
  const metadata = useMetadata();
  const conversations = useConversations();
  const sources = useSources();
  const sidebar = useDrawer("conversation-sidebar");
  const library = useDrawer("sources-panel");
  const chat = useChatSelection(conversations.insert, sidebar.close);
  const creation = useCreateConversation(chat.created);
  const scope = effectiveScope(chat.scope, sources.searchable);
  return (
    <div className="app-shell">
      <ConversationSidebar
        conversations={conversations}
        chat={chat}
        creation={creation}
        scope={scope}
        drawer={sidebar}
      />
      <main className="workspace">
        <WorkspaceHeader
          metadata={metadata}
          searchable={sources.searchable}
          sidebar={sidebar}
          library={library}
        />
        <WorkspaceNotices metadata={metadata} sources={sources} creation={creation} library={library} />
        <ActiveChat
          chat={chat}
          agent={metadata.agent}
          creation={creation}
          refresh={conversations.refresh}
          sources={sources.searchable}
          scope={scope}
        />
      </main>
      <SourcesDrawer drawer={library} sources={sources} agent={metadata.agent} />
    </div>
  );
}

/** A drawer's open state; while open, focus moves in, Tab stays inside, and Escape closes. */
function useDrawer(drawerId: string) {
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);
  useDrawerAccessibility(open, close, drawerId);
  return { open, show: () => setOpen(true), close };
}

type Drawer = ReturnType<typeof useDrawer>;

/**
 * The open conversation, its first draft, and its sources. Each conversation keeps its own
 * sources; a new one starts with all unless the current ones are kept.
 */
function useChatSelection(insert: (conversation: Conversation) => void, opened: () => void) {
  const [selected, setSelected] = useState(readSelection);
  const [initialDraft, setInitialDraft] = useState("");
  const [scope, setScope] = useState(() => readScope(readSelection()));

  function open(id: string, nextScope: string[] | null) {
    setSelected(id);
    saveSelection(id);
    setScope(nextScope);
    setInitialDraft("");
    opened();
  }

  function changeScope(next: string[] | null) {
    setScope(next);
    if (selected !== null) saveScope(selected, next);
  }

  function created(conversation: Conversation, draft: string, keepScope: boolean) {
    insert(conversation);
    const next = keepScope ? scope : null;
    saveScope(conversation.id, next);
    open(conversation.id, next);
    setInitialDraft(draft);
  }

  const select = (id: string) => open(id, readScope(id));
  return { selected, initialDraft, scope, select, changeScope, created };
}

type ChatSelection = ReturnType<typeof useChatSelection>;

interface ConversationSidebarProps {
  conversations: Conversations;
  chat: ChatSelection;
  creation: Creation;
  scope: SearchScope;
  drawer: Drawer;
}

/** The conversation drawer and its backdrop. */
function ConversationSidebar(props: ConversationSidebarProps) {
  const { conversations, chat, creation, drawer } = props;
  return (
    <>
      <Sidebar
        items={conversations.items}
        selected={chat.selected}
        loading={conversations.loading}
        error={conversations.error}
        hasMore={conversations.cursor !== null}
        creating={creation.pending}
        narrowed={narrowedCount(props.scope)}
        open={drawer.open}
        select={chat.select}
        create={(keepScope) => creation.create("", keepScope)}
        more={conversations.more}
        refresh={conversations.refresh}
        close={drawer.close}
      />
      <Backdrop open={drawer.open} close={drawer.close} label="Close conversation sidebar" />
    </>
  );
}

/** How many sources a narrowed scope searches; null when it searches all or none is left. */
function narrowedCount(scope: SearchScope): number | null {
  if (scope.ids === null || scope.unavailable) return null;
  return scope.ids.length;
}

interface WorkspaceHeaderProps {
  metadata: Metadata;
  searchable: Source[];
  sidebar: Drawer;
  library: Drawer;
}

function WorkspaceHeader({ metadata, searchable, sidebar, library }: WorkspaceHeaderProps) {
  return (
    <header className="workspace-header">
      <button
        className="icon-button mobile-menu"
        aria-label="Conversations"
        aria-expanded={sidebar.open}
        aria-controls="conversation-sidebar"
        onClick={sidebar.show}
      >
        <Icon name="menu" />
      </button>
      <span className="header-brand-mark" aria-hidden="true">
        <Icon name="mesh" />
      </span>
      <div className="agent-heading">
        <h1>{AGENT_NAME}</h1>
        <p>{retrievalSummary(searchable)}</p>
      </div>
      <button
        className="sources-button"
        aria-expanded={library.open}
        aria-controls="sources-panel"
        onClick={library.show}
      >
        <Icon name="library" />
        Sources
      </button>
      <AgentStatus agent={metadata.agent} refresh={metadata.refresh} />
    </header>
  );
}

interface WorkspaceNoticesProps {
  metadata: Metadata;
  sources: SourcesState;
  creation: Creation;
  library: Drawer;
}

/** Setup guidance and the errors that block chatting, above the conversation. */
function WorkspaceNotices({ metadata, sources, creation, library }: WorkspaceNoticesProps) {
  return (
    <>
      <SetupBanner agent={metadata.agent} refresh={metadata.refresh} />
      <SourcesHint sources={sources} open={library.show} />
      <div className="workspace-feedback">
        <ErrorNotice message={metadata.error} retry={metadata.refresh} />
        <ErrorNotice message={creation.error} retry={creation.retry} />
      </div>
    </>
  );
}

function retrievalSummary(searchable: Source[]): string {
  if (searchable.length === 1) return "Agentic retrieval · 1 searchable source";
  return `Agentic retrieval · ${searchable.length} searchable sources`;
}

function SourcesHint({ sources, open }: { sources: SourcesState; open: () => void }) {
  if (sources.loading || sources.searchable.length > 0) return null;
  return (
    <div className="setup-banner sources-hint" role="status">
      <div className="setup-symbol">
        <Icon name="library" />
      </div>
      <div>
        <strong>Add documents to get cited answers</strong>
        <p>
          Upload files (PDF, Office, HTML, Markdown, text) or add a website.
          Until something is indexed, the agent
          reports an evidence gap instead of guessing.
        </p>
      </div>
      <button className="text-button" onClick={open}>
        Open sources
      </button>
    </div>
  );
}

interface ActiveChatProps {
  chat: ChatSelection;
  agent: AgentMetadata | null;
  creation: Creation;
  refresh: () => void;
  sources: Source[];
  scope: SearchScope;
}

function ActiveChat({ chat, agent, creation, refresh, sources, scope }: ActiveChatProps) {
  if (chat.selected === null)
    return (
      <div className="chat-scroll initial-welcome">
        <Welcome suggest={creation.create} disabled={creation.pending} />
      </div>
    );
  return (
    <ChatPanel
      key={chat.selected}
      conversationId={chat.selected}
      initialDraft={chat.initialDraft}
      agent={agent}
      sources={sources}
      scope={scope}
      changeScope={chat.changeScope}
      onComplete={refresh}
    />
  );
}

interface SourcesDrawerProps {
  drawer: Drawer;
  sources: SourcesState;
  agent: AgentMetadata | null;
}

/** The sources drawer and its backdrop. */
function SourcesDrawer({ drawer, sources, agent }: SourcesDrawerProps) {
  return (
    <>
      <Backdrop open={drawer.open} close={drawer.close} label="Close sources" />
      <SourcesPanel open={drawer.open} close={drawer.close} sources={sources} {...uploadRules(agent)} />
    </>
  );
}

/** The server's upload rules; before the metadata loads, any type up to the default size. */
function uploadRules(agent: AgentMetadata | null): { maxUploadBytes: number; accept: string } {
  if (agent === null) return { maxUploadBytes: DEFAULT_UPLOAD_LIMIT, accept: "" };
  return {
    maxUploadBytes: agent.limits.max_upload_bytes,
    accept: agent.supported_media_types.join(","),
  };
}

function Backdrop(props: { open: boolean; close: () => void; label: string }) {
  if (!props.open) return null;
  return (
    <button
      className="drawer-backdrop"
      onClick={props.close}
      aria-label={props.label}
      tabIndex={-1}
    />
  );
}

// Drawers (conversations, sources): focus moves in, Tab stays inside, Escape closes.

function focusableElements(sidebar: HTMLElement): HTMLElement[] {
  return [
    ...sidebar.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input:not(:disabled), summary, a[href], [tabindex="0"]',
    ),
  ];
}

function focusStart(sidebar: HTMLElement) {
  const elements = focusableElements(sidebar);
  if (elements.length > 0) elements[0].focus();
}

function tabDestination(backward: boolean, elements: HTMLElement[]) {
  const first = elements[0];
  const last = elements[elements.length - 1];
  if (backward) return { boundary: first, next: last };
  return { boundary: last, next: first };
}

function trapTab(event: KeyboardEvent, sidebar: HTMLElement) {
  const elements = focusableElements(sidebar);
  if (elements.length === 0) return;
  const target = tabDestination(event.shiftKey, elements);
  if (document.activeElement === target.boundary) {
    event.preventDefault();
    target.next.focus();
  }
}

function handleKey(
  event: KeyboardEvent,
  sidebar: HTMLElement,
  close: () => void,
) {
  if (event.key === "Escape") {
    close();
    return;
  }
  if (event.key === "Tab") trapTab(event, sidebar);
}

export function useDrawerAccessibility(
  open: boolean,
  close: () => void,
  drawerId: string,
) {
  useEffect(() => {
    if (!open) return;
    const sidebar = document.getElementById(drawerId);
    if (sidebar === null) return;
    const previous = document.activeElement;
    focusStart(sidebar);
    const onKeyDown = (event: KeyboardEvent) =>
      handleKey(event, sidebar, close);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      if (previous instanceof HTMLElement) previous.focus();
    };
  }, [open, close, drawerId]);
}
