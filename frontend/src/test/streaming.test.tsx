import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { sendMessage } from "../api/client";
import { createHttp, emptyHistory, first, selectConversation, turn } from "./fixtures";

const encoder = new TextEncoder();
function frame(event: string, data: unknown, newline = "\n") {
  return `event: ${event}${newline}data: ${JSON.stringify(data)}${newline}${newline}`;
}

function controlledStream() {
  let controller: ReadableStreamDefaultController<Uint8Array>;
  const cancelled = vi.fn();
  const body = new ReadableStream<Uint8Array>({
    start(value) { controller = value; },
    cancel: cancelled,
  });
  return {
    response: new Response(body, { headers: { "Content-Type": "text/event-stream" } }),
    send: (text: string) => controller.enqueue(encoder.encode(text)),
    close: () => controller.close(),
    cancelled,
  };
}

async function submitQuestion() {
  const input = await screen.findByLabelText("Message ContextMesh Agent");
  await waitFor(() => expect((input as HTMLTextAreaElement).disabled).toBe(false));
  fireEvent.change(input, { target: { value: "Help me plan" } });
  fireEvent.click(screen.getByRole("button", { name: "Send message" }));
  return input as HTMLTextAreaElement;
}

describe("answer streaming boundary", () => {
  it("decodes byte-split UTF-8, CRLF, comments, multiline data, and authoritative completion", async () => {
    const wire = ": heartbeat\r\n\r\nevent: progress\r\ndata: {\r\ndata: \"stage\": \"direct\", \"message\": \"Writing\"}\r\n\r\n" +
      frame("delta", { text: "Café 🌱" }, "\r\n") + frame("completed", turn());
    const bytes = encoder.encode(wire);
    vi.stubGlobal("fetch", async () => new Response(new ReadableStream({
      start(controller) {
        for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
        controller.close();
      },
    }), { headers: { "Content-Type": "text/event-stream" } }));
    const receive = vi.fn();
    const result = await sendMessage(first.id, { message: "Help me plan", sourceIds: null },
      "key", new AbortController().signal, receive);
    expect(receive.mock.calls).toEqual([
      [{ event: "progress", data: { stage: "direct", message: "Writing" } }],
      [{ event: "delta", data: { text: "Café 🌱" } }],
    ]);
    expect(result).toEqual(turn());
  });

  it.each([
    ["truncated", frame("delta", { text: "Partial" }), "stream_interrupted"],
    ["malformed", "event: delta\ndata: {oops}\n\n", "stream_invalid"],
    ["invalid event", frame("delta", { text: 8 }), "stream_invalid"],
    ["invalid completed message", frame("completed", {
      ...turn(), assistant_message: { id: "a", role: "assistant", content: "Crash" },
    }), "stream_invalid"],
    ["invalid completed citations", frame("completed", {
      ...turn(), assistant_message: { ...turn().assistant_message,
        answer: { status: "answered", gaps: [], claims: [], citations: [{ id: "bad" }] } },
    }), "stream_invalid"],
    ["wrong conversation", frame("completed", { ...turn(), conversation_id: "other" }), "stream_invalid"],
    ["oversized frame", "data: " + "x".repeat(1_048_577), "stream_invalid"],
    ["terminal error", frame("error", { code: "provider_unavailable", message: "secret", retryable: true }), "provider_unavailable"],
  ])("rejects %s without posting a second turn", async (_name, wire, code) => {
    const fetch = vi.fn(async () => new Response(wire, {
      headers: { "Content-Type": "text/event-stream" },
    }));
    vi.stubGlobal("fetch", fetch);
    await expect(sendMessage(first.id, { message: "Hello", sourceIds: null },
      "key", new AbortController().signal)).rejects.toMatchObject({ code });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});

describe("streaming conversation experience", () => {
  it("shows the first question and incremental Markdown before replacing it with saved messages", async () => {
    selectConversation();
    const stream = controlledStream();
    vi.stubGlobal("fetch", createHttp(async (_url, init) =>
      init.method === "POST" ? stream.response : emptyHistory()));
    render(<App />);
    const input = await submitQuestion();
    expect(screen.getByText("Help me plan", { selector: ".message-content" })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Start chatting" })).toBeNull();
    expect(input.disabled).toBe(true);
    await act(async () => stream.send(frame("progress", { stage: "direct", message: "Composing your answer…" }) +
      frame("delta", { text: "## Draft answer\n\nFirst **tokens**" })));
    expect(await screen.findByRole("heading", { name: "Draft answer" })).toBeTruthy();
    const pending = screen.getByRole("article", { name: "Answer in progress" });
    expect(within(pending).getByText("tokens").tagName).toBe("STRONG");
    expect(screen.getByText("Composing your answer…")).toBeTruthy();
    await act(async () => stream.send(frame("completed", turn("## Final answer\n\nSaved text."))));
    expect(await screen.findByRole("heading", { name: "Final answer" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Draft answer" })).toBeNull();
    expect(screen.getAllByText("Help me plan")).toHaveLength(1);
    expect(screen.getAllByRole("article")).toHaveLength(2);
    expect(input.disabled).toBe(false);
    expect(stream.cancelled).toHaveBeenCalledTimes(1);
  });

  it.each(["interrupted", "failed", "malformed completion"])("discards %s drafts and retries the identical request and key", async (kind) => {
    selectConversation();
    const stream = controlledStream();
    const requests: RequestInit[] = [];
    vi.stubGlobal("fetch", createHttp(async (_url, init) => {
      if (init.method !== "POST") return emptyHistory();
      requests.push(init);
      return requests.length === 1 ? stream.response : new Response(frame("completed", turn()),
        { headers: { "Content-Type": "text/event-stream" } });
    }));
    render(<App />);
    const input = await submitQuestion();
    await act(async () => stream.send(frame("delta", { text: "Unfinished draft" })));
    expect(await screen.findByText("Unfinished draft")).toBeTruthy();
    await act(async () => {
      if (kind === "failed") stream.send(frame("error", {
        code: "provider_unavailable", message: "secret upstream error", retryable: true,
      }));
      else if (kind === "malformed completion") stream.send(frame("completed", {
        ...turn(), assistant_message: { ...turn().assistant_message, answer: {} },
      }));
      else stream.close();
    });
    const retry = await screen.findByRole("button", { name: "Retry message" });
    expect(screen.queryByText("Unfinished draft")).toBeNull();
    expect(screen.queryByText("secret upstream error")).toBeNull();
    expect(input.value).toBe("Help me plan");
    fireEvent.click(retry);
    await screen.findByText("A useful answer.");
    expect(requests[0].headers).toEqual(requests[1].headers);
    expect(requests[0].body).toBe(requests[1].body);
  });

  it("aborts and cancels the active reader when another conversation is selected", async () => {
    selectConversation();
    const stream = controlledStream();
    let signal: AbortSignal | null | undefined;
    vi.stubGlobal("fetch", createHttp(async (_url, init) => {
      if (init.method !== "POST") return emptyHistory();
      signal = init.signal;
      return stream.response;
    }));
    render(<App />);
    await submitQuestion();
    await act(async () => stream.send(frame("delta", { text: "Old stream" })));
    await screen.findByText("Old stream");
    fireEvent.click(screen.getByRole("button", { name: "Open conversation: Another conversation" }));
    await waitFor(() => expect(signal?.aborted).toBe(true));
    expect(stream.cancelled).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Old stream")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

});
