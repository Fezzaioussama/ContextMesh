import type { ComponentProps } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "../message-content.css";

/** Model text cannot create local citation anchors, active HTML, or image requests. */
function safeLink(url: string): string {
  try {
    const parsed = new URL(url);
    return ["https:", "http:", "mailto:"].includes(parsed.protocol) ? url : "";
  } catch {
    return "";
  }
}

function MarkdownLink({ href, children }: ComponentProps<"a">) {
  if (!href) return <span>{children}</span>;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  );
}

/**
 * Renders model text as GitHub-flavored Markdown with raw HTML skipped, only http(s) and
 * mailto links, images shown as their alt text, and keyboard-scrollable code and tables.
 */
export function Markdown({ children }: { children: string }) {
  return (
    <div className="markdown-content">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        urlTransform={safeLink}
        components={{
          a: MarkdownLink,
          img: ({ alt }) => <span className="markdown-image-label">{alt ?? "Image"}</span>,
          pre: ({ children }) => (
            <pre tabIndex={0} aria-label="Code block">{children}</pre>
          ),
          table: ({ children }) => (
            <div className="markdown-table" tabIndex={0} role="region" aria-label="Scrollable table">
              <table>{children}</table>
            </div>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
