import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown } from "../components/Markdown";
import { MessageList } from "../features/chat/Messages";
import { groundedAnswer, message } from "./fixtures";

describe("safe answer Markdown", () => {
  it("renders headings, emphasis, lists, tables, fenced code and external links", () => {
    const { container } = render(<Markdown>{[
      "## Release plan", "", "A **clear** plan with `inline code`.", "",
      "- First", "- Second", "", "| Item | Status |", "| --- | --- |", "| API | Ready |", "",
      "```typescript", "const ready = true;", "```", "", "[Read more](https://example.com/docs)",
    ].join("\n")}</Markdown>);
    expect(screen.getByRole("heading", { name: "Release plan", level: 2 })).toBeTruthy();
    expect(screen.getByRole("table").textContent).toContain("APIReady");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(container.querySelector("strong")?.textContent).toBe("clear");
    expect(container.querySelector("pre code")?.textContent).toContain("const ready = true;");
    expect(screen.getByRole("link", { name: "Read more" }).getAttribute("rel"))
      .toBe("noopener noreferrer");
  });

  it("disables active HTML, hostile URLs, model citation links and image fetches", () => {
    const { container } = render(<Markdown>{[
      "<script>alert('secret')</script>", "",
      '<img src="https://tracker.example/pixel" onerror="alert(1)">', "",
      "[bad](javascript:alert%281%29)", "[data](data:text/html,boom)",
      "[fake citation](#a-citation-1)", "[relative](/api/v1/sources)",
      "![diagram](https://tracker.example/diagram.png)",
    ].join("\n")}</Markdown>);
    expect(container.querySelector("script, img, iframe")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
    expect(screen.getByText("diagram")).toBeTruthy();
    expect(container.textContent).not.toContain("secret");
  });

  it("formats claims while reserving citation anchors for canonical references", () => {
    const answer = {
      ...groundedAnswer,
      claims: [{ id: "c", text: "### Use **OIDC**\n\n[forged](#a-citation-1)",
        citation_ids: ["citation-1", "unknown"] }],
    };
    const { container } = render(
      <MessageList messages={[message("a", "assistant", "fallback", answer)]} pending={null} />,
    );
    expect(screen.getByRole("heading", { name: "Use OIDC" })).toBeTruthy();
    const links = screen.getAllByRole("link");
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute("href")).toBe("#a-citation-1");
    expect(links[0].getAttribute("aria-label")).toBe("Citation 1");
    expect(container.querySelector("p div")).toBeNull();
  });
});
