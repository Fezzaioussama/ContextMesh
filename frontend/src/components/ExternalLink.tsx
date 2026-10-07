import type { ReactNode } from "react";

/** Only http(s) URLs become links; anything else renders as plain, inert text. */
export function webUrl(value: string | null): string | null {
  if (value === null) return null;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

export function ExternalLink({
  href,
  children,
}: {
  href: string | null;
  children: ReactNode;
}) {
  const safe = webUrl(href);
  if (safe === null) return null;
  return (
    <a className="external-link" href={safe} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  );
}
