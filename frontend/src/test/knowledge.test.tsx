import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { documentStatus } from "../features/sources/documentStatus";
import {
  createHttp,
  decisions,
  documentSummary,
  emptyHistory,
  groundedAnswer,
  handbook,
  indexJob,
  json,
  message,
  selectConversation,
  turn,
} from "./fixtures";

async function readyComposer() {
  const input = await screen.findByLabelText("Message ContextMesh Agent");
  await waitFor(() => expect((input as HTMLTextAreaElement).disabled).toBe(false));
  return input as HTMLTextAreaElement;
}

describe("grounded answers", () => {
  it("renders claims, linked citations, gaps, and loads the full passage", async () => {
    selectConversation();
    const answer = message("a", "assistant", "We use OIDC. [1]", groundedAnswer);
    vi.stubGlobal(
      "fetch",
      createHttp(async (url) => {
        if (url.includes("/evidence"))
          return json({ ...groundedAnswer.citations[0], text: "Full passage text." });
        return json({ items: [message("q", "user", "Auth?"), answer], next_cursor: null });
      }),
    );
    render(<App />);
    expect(await screen.findByText("Partial answer")).toBeTruthy();
    const marker = screen.getByRole("link", { name: "Citation 1" });
    expect(marker.getAttribute("href")).toBe("#a-citation-1");
    expect(elementById("a-citation-1").textContent).toContain("Auth › Decision · lines 3–4");
    expect(screen.getByText("The rollout date is not documented.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Show full passage" }));
    expect(await screen.findByText("Full passage text.")).toBeTruthy();
  });

  it("shows a withheld answer without claims or citations", async () => {
    selectConversation();
    const withheld = message("w", "assistant", "This answer is unavailable.", {
      status: "withheld",
      claims: [],
      citations: [],
      gaps: [],
    });
    vi.stubGlobal(
      "fetch",
      createHttp(async () => json({ items: [withheld], next_cursor: null })),
    );
    render(<App />);
    expect(await screen.findByText("Answer withheld")).toBeTruthy();
    expect(screen.getByText("This answer is unavailable.")).toBeTruthy();
    expect(screen.queryByRole("list", { name: "Citations" })).toBeNull();
  });
});

describe("search scope", () => {
  it("sends the selected sources and retries with the identical payload", async () => {
    selectConversation();
    const requests: RequestInit[] = [];
    const http = createHttp(
      async (_url, init) => {
        if (init.method !== "POST") return emptyHistory();
        requests.push(init);
        if (requests.length === 1) throw new TypeError("network failure");
        return json(turn("Scoped answer."));
      },
      true,
      [handbook, decisions],
    );
    vi.stubGlobal("fetch", http);
    render(<App />);
    const input = await readyComposer();
    fireEvent.click(await screen.findByText("Searching all 2 sources"));
    fireEvent.click(screen.getByLabelText("Handbook"));
    expect(screen.getByText("Searching 1 of 2 sources")).toBeTruthy();
    fireEvent.change(input, { target: { value: "Where is auth?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    fireEvent.click(await screen.findByRole("button", { name: "Retry message" }));
    await screen.findByText("Scoped answer.");
    expect(JSON.parse(String(requests[1].body))).toEqual({
      message: "Where is auth?",
      source_ids: [handbook.id],
    });
    expect(requests[0].headers).toEqual(requests[1].headers);
  });

  it("uses a new idempotency key when the scope changes before a retry", async () => {
    selectConversation();
    const requests: RequestInit[] = [];
    vi.stubGlobal(
      "fetch",
      createHttp(
        async (_url, init) => {
          if (init.method !== "POST") return emptyHistory();
          requests.push(init);
          if (requests.length === 1) throw new TypeError("network failure");
          return json(turn());
        },
        true,
        [handbook, decisions],
      ),
    );
    render(<App />);
    const input = await readyComposer();
    fireEvent.change(input, { target: { value: "Where is auth?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await screen.findByRole("button", { name: "Retry message" });
    fireEvent.click(screen.getByText("Searching all 2 sources"));
    fireEvent.click(screen.getByLabelText("Decisions"));
    fireEvent.click(screen.getByRole("button", { name: "Retry message" }));
    await screen.findByText("A useful answer.");
    const keys = requests.map((item) => new Headers(item.headers).get("Idempotency-Key"));
    expect(keys[0]).not.toBe(keys[1]);
  });
});

describe("deleted scoped sources", () => {
  it("never widens an explicit selection after its sources disappear", async () => {
    selectConversation();
    let available = [handbook, decisions];
    const removeSource = async (url: string) => {
      available = available.filter((source) => !url.endsWith(source.id));
      return json({ job_id: "cleanup" }, 202);
    };
    vi.stubGlobal("fetch", async (input: string, init: RequestInit = {}) =>
      createHttp(
        async (url, request) =>
          request.method === "DELETE" ? removeSource(url) : emptyHistory(),
        true,
        available,
      )(input, init),
    );
    render(<App />);
    await readyComposer();
    fireEvent.click(await screen.findByText("Searching all 2 sources"));
    fireEvent.click(screen.getByLabelText("Handbook"));
    fireEvent.click(screen.getByRole("button", { name: "Sources" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete source Handbook" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(
      await screen.findByText("Selected sources are no longer searchable"),
    ).toBeTruthy();
    const send = screen.getByRole("button", { name: "Send message" });
    expect((send as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("knowledge sources", () => {
  it("suggests adding documents only while nothing is searchable", async () => {
    vi.stubGlobal("fetch", createHttp(emptyHistory));
    render(<App />);
    expect(await screen.findByText("Add documents to get cited answers")).toBeTruthy();
    expect(screen.getByText("Agentic retrieval · 0 searchable sources")).toBeTruthy();
  });

  it("creates a source, uploads a file, and polls until it is indexed", async () => {
    let listed = 0;
    const posts: { url: string; init: RequestInit }[] = [];
    vi.stubGlobal(
      "fetch",
      createHttp(
        async (url, init) => {
          if (init.method === "POST") {
            posts.push({ url, init });
            return json(handbook, 201);
          }
          listed += 1;
          return json({ items: listedDocuments(listed) });
        },
        true,
        [handbook],
      ),
    );
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Sources" }));
    fireEvent.change(screen.getByLabelText("Source name"), { target: { value: "Team docs" } });
    fireEvent.click(screen.getByRole("button", { name: "Add source" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(JSON.parse(String(posts[0].init.body))).toEqual({ name: "Team docs", description: "" });
    const file = new File(["# Auth\n\nWe use OIDC."], "auth.md", { type: "text/markdown" });
    fireEvent.change(screen.getByLabelText("Upload files to Handbook"), { target: { files: [file] } });
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[1].url).toBe(`/api/v1/sources/${handbook.id}/documents`);
    expect((posts[1].init.body as FormData).get("file")).toBe(file);
    expect(await screen.findByText("Indexing…")).toBeTruthy();
    expect(await screen.findByText("Ready · 3 passages", {}, { timeout: 4000 })).toBeTruthy();
  });

  it("rejects an oversized file before sending it", async () => {
    const posts = vi.fn();
    vi.stubGlobal(
      "fetch",
      createHttp(
        async (_url, init) => {
          if (init.method === "POST") posts();
          return json({ items: [] });
        },
        true,
        [handbook],
      ),
    );
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Sources" }));
    const large = new File(["x".repeat(2_000_001)], "large.md", { type: "text/markdown" });
    fireEvent.change(await screen.findByLabelText("Upload files to Handbook"), {
      target: { files: [large] },
    });
    expect(
      await screen.findByText("This file is larger than the server's upload limit."),
    ).toBeTruthy();
    expect(posts).not.toHaveBeenCalled();
  });

  it("explains failed and retrying indexing jobs in plain language", () => {
    const failed = documentSummary({
      searchable: false,
      latest_job: indexJob({ status: "failed", error_code: "invalid_encoding" }),
    });
    const retrying = documentSummary({
      latest_job: indexJob({ status: "queued", error_code: "provider_unavailable" }),
    });
    expect(documentStatus(failed)).toEqual({
      label: "Failed: the file is not UTF-8 text",
      tone: "error",
    });
    expect(documentStatus(retrying).label).toBe("Retrying…");
  });
});

/** First load: empty; after upload: queued; then indexed. */
function listedDocuments(call: number) {
  if (call < 2) return [];
  if (call < 3)
    return [documentSummary({ searchable: false, latest_job: indexJob({ status: "queued" }) })];
  return [documentSummary()];
}

function elementById(id: string): HTMLElement {
  const element = document.getElementById(id);
  if (element === null) throw new Error(`Missing element ${id}`);
  return element;
}
