import { Icon } from "../../components/Icon";

const suggestions = [
  {
    title: "Make a plan",
    text: "Help me break down a complex project into practical next steps.",
    icon: "01",
  },
  {
    title: "Understand an idea",
    text: "Explain dependency inversion with a clear, everyday example.",
    icon: "02",
  },
  {
    title: "Think it through",
    text: "Help me compare the tradeoffs of an important decision.",
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
      <p className="eyebrow">A LITTLE CLARITY STARTS HERE</p>
      <h2>What are you working on?</h2>
      <p className="welcome-description">
        A space to explore ideas, untangle questions,
        <br className="desktop-break" /> and find your next step.
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
        Provider chat · knowledge retrieval not connected
      </p>
    </section>
  );
}
