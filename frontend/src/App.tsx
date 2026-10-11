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

export function App() {
  const metadata = useMetadata();
  const conversations = useConversations();
  const sources = useSources();
  const [selected, setSelected] = useState(readSelection);
  const [initialDraft, setInitialDraft] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  // Each conversation keeps its own sources; a new one starts with all unless kept.
  const [scope, setScope] = useState(() => readScope(readSelection()));
  const closeSidebar = useCallback(() => setSidebarOpen(false), []);
  const closeSources = useCallback(() => setSourcesOpen(false), []);
  useDrawerAccessibility(sidebarOpen, closeSidebar, "conversation-sidebar");
  useDrawerAccessibility(sourcesOpen, closeSources, "sources-panel");

  function open(id: string, nextScope: string[] | null) {
    setSelected(id);
    saveSelection(id);
    setScope(nextScope);
    setInitialDraft("");
    setSidebarOpen(false);
  }

  const select = (id: string) => open(id, readScope(id));

  function changeScope(next: string[] | null) {
    setScope(next);
    if (selected !== null) saveScope(selected, next);
  }

  function created(conversation: Conversation, draft: string, keepScope: boolean) {
    conversations.insert(conversation);
    const next = keepScope ? scope : null;
    saveScope(conversation.id, next);
    open(conversation.id, next);
    setInitialDraft(draft);
  }

  const creation = useCreateConversation(created);
  const currentScope = effectiveScope(scope, sources.searchable);
  const narrowed =
    currentScope.ids === null || currentScope.unavailable ? null : currentScope.ids.length;

  return (
    <div className="app-shell">
      <Sidebar
        items={conversations.items}
        selected={selected}
        loading={conversations.loading}
        error={conversations.error}
        hasMore={conversations.cursor !== null}
        creating={creation.pending}
        narrowed={narrowed}
        open={sidebarOpen}
        select={select}
        create={(keepScope) => creation.create("", keepScope)}
        more={conversations.more}
        refresh={conversations.refresh}
        close={closeSidebar}
      />
      <Backdrop open={sidebarOpen} close={closeSidebar} label="Close conversation sidebar" />
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
            <h1>{AGENT_NAME}</h1>
            <p>{retrievalSummary(sources.searchable)}</p>
          </div>
          <button
            className="sources-button"
            aria-expanded={sourcesOpen}
            aria-controls="sources-panel"
            onClick={() => setSourcesOpen(true)}
          >
            <Icon name="library" />
            Sources
          </button>
          <AgentStatus agent={metadata.agent} refresh={metadata.refresh} />
        </header>
        <SetupBanner agent={metadata.agent} refresh={metadata.refresh} />
        <SourcesHint sources={sources} open={() => setSourcesOpen(true)} />
        <div className="workspace-feedback">
          <ErrorNotice message={metadata.error} retry={metadata.refresh} />
          <ErrorNotice message={creation.error} retry={creation.retry} />
        </div>
        <ActiveChat
          selected={selected}
          initialDraft={initialDraft}
          agent={metadata.agent}
          create={creation.create}
          creating={creation.pending}
          refresh={conversations.refresh}
          sources={sources.searchable}
          scope={currentScope}
          changeScope={changeScope}
        />
      </main>
      <Backdrop open={sourcesOpen} close={closeSources} label="Close sources" />
      <SourcesPanel
        open={sourcesOpen}
        close={closeSources}
        sources={sources}
        maxUploadBytes={metadata.agent?.limits.max_upload_bytes ?? DEFAULT_UPLOAD_LIMIT}
        accept={metadata.agent?.supported_media_types.join(",") ?? ""}
      />
    </div>
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
  selected: string | null;
  initialDraft: string;
  agent: AgentMetadata | null;
  creating: boolean;
  create: (draft: string) => void;
  refresh: () => void;
  sources: Source[];
  scope: SearchScope;
  changeScope: (scope: string[] | null) => void;
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
      sources={props.sources}
      scope={props.scope}
      changeScope={props.changeScope}
      onComplete={props.refresh}
    />
  );
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
