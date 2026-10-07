import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { App } from "../App";
import {
  agent,
  createHttp,
  emptyHistory,
  first,
  json,
  message,
  second,
  selectConversation,
  turn,
} from "./fixtures";

describe("saved conversation boundaries", () => {
  it("loads every persisted history page in order", async () => {
    selectConversation();
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      createHttp(async (url) => {
        urls.push(url);
        if (url.includes("cursor=next"))
          return json({
            items: [message("new", "assistant", "Newest saved reply")],
            next_cursor: null,
          });
        return json({
          items: [message("old", "user", "Oldest saved question")],
          next_cursor: "next",
        });
      }),
    );
    render(<App />);
    await screen.findByText("Newest saved reply");
    const contents = screen.getAllByRole("article");
    expect(contents[0].textContent).toContain("Oldest saved question");
    expect(contents[1].textContent).toContain("Newest saved reply");
    expect(urls).toHaveLength(2);
  });

  it("ignores stale history that resolves after selecting another conversation", async () => {
    selectConversation();
    let resolve: (response: Response) => void = () => {};
    const stale = new Promise<Response>((done) => {
      resolve = done;
    });
    vi.stubGlobal(
      "fetch",
      createHttp(async (url) => {
        if (url.includes(first.id)) return stale;
        return json({
          items: [message("second", "assistant", "Second conversation reply")],
          next_cursor: null,
        });
      }),
    );
    render(<App />);
    await screen.findByText("Loading conversation…");
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Open conversation: Another conversation",
      }),
    );
    await screen.findByText("Second conversation reply");
    await act(async () =>
      resolve(
        json({
          items: [message("stale", "assistant", "Stale response")],
          next_cursor: null,
        }),
      ),
    );
    expect(screen.queryByText("Stale response")).toBeNull();
    expect(localStorage.getItem("contextmesh.selected-conversation")).toBe(
      second.id,
    );
  });

  it("ignores a completed turn after switching and retains its key for a safe retry", async () => {
    selectConversation();
    let resolve: (response: Response) => void = () => {};
    const stale = new Promise<Response>((done) => {
      resolve = done;
    });
    const requests: RequestInit[] = [];
    vi.stubGlobal(
      "fetch",
      createHttp(async (_url, init) => {
        if (init.method !== "POST") return emptyHistory();
        requests.push(init);
        if (requests.length === 1) return stale;
        return json(turn());
      }),
    );
    render(<App />);
    const input = await screen.findByLabelText("Message ContextMesh Agent");
    await waitFor(() =>
      expect((input as HTMLTextAreaElement).disabled).toBe(false),
    );
    fireEvent.change(input, { target: { value: "Help me plan" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    fireEvent.click(
      screen.getByRole("button", {
        name: "Open conversation: Another conversation",
      }),
    );
    await act(async () => resolve(json(turn("Old conversation completion"))));
    expect(screen.queryByText("Old conversation completion")).toBeNull();
    fireEvent.click(
      screen.getByRole("button", { name: "Open conversation: Planning notes" }),
    );
    const restored = await screen.findByLabelText(
      "Message ContextMesh Agent",
    );
    await waitFor(() =>
      expect((restored as HTMLTextAreaElement).disabled).toBe(false),
    );
    expect((restored as HTMLTextAreaElement).value).toBe("Help me plan");
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await screen.findByText("A useful answer.");
    expect(requests[0].headers).toEqual(requests[1].headers);
  });

  it("shows a safe failure without disclosing arbitrary server text", async () => {
    selectConversation();
    vi.stubGlobal(
      "fetch",
      createHttp(async (_url, init) => {
        if (init.method !== "POST") return emptyHistory();
        return json(
          {
            error: {
              code: "provider_unavailable",
              message: "secret provider exception",
              retryable: true,
              request_id: "request",
            },
          },
          503,
        );
      }),
    );
    render(<App />);
    const input = await screen.findByLabelText("Message ContextMesh Agent");
    await waitFor(() =>
      expect((input as HTMLTextAreaElement).disabled).toBe(false),
    );
    fireEvent.change(input, { target: { value: "Help me plan" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByText("secret provider exception")).toBeNull();
    expect((input as HTMLTextAreaElement).value).toBe("Help me plan");
  });

  it("loads another page of saved conversations", async () => {
    vi.stubGlobal("fetch", async (input: string) => {
      if (input === "/api/v1/assistant") return json(agent);
      if (input === "/api/v1/sources") return json({ items: [] });
      if (input.includes("cursor=next"))
        return json({ items: [second], next_cursor: null });
      return json({ items: [first], next_cursor: "next" });
    });
    render(<App />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Load more conversations" }),
    );
    await screen.findByRole("button", {
      name: "Open conversation: Another conversation",
    });
    expect(
      screen.getByRole("button", { name: "Open conversation: Planning notes" }),
    ).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Load more conversations" }),
    ).toBeNull();
  });

  it("supports keyboard dismissal and focus return for the mobile sidebar", async () => {
    vi.stubGlobal("fetch", createHttp(emptyHistory));
    render(<App />);
    const menu = screen.getByRole("button", { name: "Conversations" });
    menu.focus();
    fireEvent.click(menu);
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "Close conversations" }),
    );
    fireEvent.keyDown(document, { key: "Escape" });
    expect(menu.getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(menu);
  });
});
