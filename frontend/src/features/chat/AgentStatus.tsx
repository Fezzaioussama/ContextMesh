import type { AgentMetadata } from "../../api/contracts";
import { Icon } from "../../components/Icon";

export function AgentStatus({
  agent,
  refresh,
}: {
  agent: AgentMetadata | null;
  refresh: () => void;
}) {
  if (agent === null)
    return <span className="model-pill">Connecting to API</span>;
  return (
    <span className="model-pill" data-configured={agent.configured}>
      <span className="status-dot" />
      {agent.model}
      <ProviderSetup agent={agent} refresh={refresh} />
    </span>
  );
}

function ProviderSetup({
  agent,
  refresh,
}: {
  agent: AgentMetadata;
  refresh: () => void;
}) {
  if (agent.configured) return null;
  return (
    <button
      className="icon-button"
      aria-label="Check provider configuration"
      onClick={refresh}
    >
      <Icon name="refresh" />
    </button>
  );
}

export function SetupBanner({
  agent,
  refresh,
}: {
  agent: AgentMetadata | null;
  refresh: () => void;
}) {
  if (agent === null) return null;
  if (agent.configured) return null;
  const apiKeyName =
    agent.provider === "openrouter" ? "OPENROUTER_API_KEY" : "OPENAI_API_KEY";
  return (
    <div className="setup-banner" role="status">
      <div className="setup-symbol">
        <Icon name="spark" />
      </div>
      <div>
        <strong>Connect your model provider</strong>
        <p>
          Set <code>{apiKeyName}</code> in the server environment, then
          restart the API.
        </p>
      </div>
      <button className="text-button" onClick={refresh}>
        Check connection
      </button>
    </div>
  );
}
