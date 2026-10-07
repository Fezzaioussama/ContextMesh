import { Icon } from "../../components/Icon";

const suggestions = [
  {
    title: "Recall a decision",
    text: "What did we decide about authentication, and why?",
    icon: "01",
  },
  {
    title: "Find a detail",
    text: "When does the rollout start, and which service goes first?",
    icon: "02",
  },
  {
    title: "Check for conflicts",
    text: "Do any documents disagree about the release plan?",
    icon: "03",
  },
];

export function Welcome({
  suggest,
  disabled,
}: {
  suggest: (message: string) => void;
  disabled: boolean;
}) {
  return (
    <section className="welcome" aria-label="Start chatting">
      <div className="welcome-symbol">
        <Icon name="mesh" />
      </div>
      <p className="eyebrow">GROUNDED IN YOUR SOURCES</p>
      <h2>What do you want to find out?</h2>
      <p className="welcome-description">
        The agent plans a search, expands it when evidence is missing, and
        cites the passages behind every claim.
      </p>
      <div className="suggestion-grid">
        {suggestions.map((suggestion) => (
          <button
            className="suggestion"
            key={suggestion.title}
            onClick={() => suggest(suggestion.text)}
            disabled={disabled}
          >
            <span className="suggestion-number">{suggestion.icon}</span>
            <strong>{suggestion.title}</strong>
            <span>{suggestion.text}</span>
            <span className="suggestion-arrow">↗</span>
          </button>
        ))}
      </div>
      <p className="welcome-note">
        Agentic retrieval · answers without evidence are reported as gaps
      </p>
    </section>
  );
}
