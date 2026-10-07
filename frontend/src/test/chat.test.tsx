import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { SetupBanner } from "../features/chat/AgentStatus";
import {
  agent,
  createHttp,
  emptyHistory,
  first,
  json,
  selectConversation,
  turn,
} from "./fixtures";

describe("assistant conversations", () => {
  it("shows the OpenRouter credential name when that provider needs setup", () => {
    render(
      <SetupBanner
        agent={{ ...agent, provider: "openrouter", configured: false }}
        refresh={vi.fn()}
      />,
    );
    expect(screen.getByText("OPENROUTER_API_KEY")).toBeTruthy();
    expect(screen.queryByText("OPENAI_API_KEY")).toBeNull();
  });

  it("sends a normalized message and renders returned HTML as inert text", async () => {
    selectConversation();
    const posts = vi.fn(async () => json(turn("<img src=x onerror=alert(1)>")));
    vi.stubGlobal(
      "fetch",
      createHttp(async (_url, init) => {
        if (init.method === "POST") return posts();
        return emptyHistory();
      }),
    );
    const { container } = render(<App />);
    const input = await screen.findByLabelText("Message ContextMesh Agent");
    await waitFor(() =>
      expect((input as HTMLTextAreaElement).disabled).toBe(false),
    );
    fireEvent.change(input, { target: { value: "  Help me plan  " } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(
      await screen.findByText("<img src=x onerror=alert(1)>"),
    ).toBeTruthy();
    expect(container.querySelector("img")).toBeNull();
    expect((input as HTMLTextAreaElement).value).toBe("");
    expect(posts).toHaveBeenCalledTimes(1);
  });

  it("preserves the draft and same idempotency key after a network failure", async () => {
    selectConversation();
    const requests: RequestInit[] = [];
    vi.stubGlobal(
      "fetch",
      createHttp(async (_url, init) => {
        if (init.method !== "POST") return emptyHistory();
        requests.push(init);
        if (requests.length === 1) throw new TypeError("network failure");
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
    const retry = await screen.findByRole("button", { name: "Retry message" });
    expect((input as HTMLTextAreaElement).value).toBe("Help me plan");
    fireEvent.click(retry);
    await screen.findByText("A useful answer.");
    expect(requests[0].headers).toEqual(requests[1].headers);
    expect(requests[0].body).toBe(requests[1].body);
    expect(JSON.parse(String(requests[0].body))).toEqual({
      message: "Help me plan",
    });
    expect(
      new Headers(requests[0].headers).get("Idempotency-Key"),
    ).toBeTruthy();
  });

  it("shows server setup guidance and disables sending without credentials", async () => {
    selectConversation();
    const fetch = vi.fn(createHttp(emptyHistory, false));
    vi.stubGlobal("fetch", fetch);
    render(<App />);
    expect(await screen.findByText("Connect your model provider")).toBeTruthy();
    expect(screen.getByText("OPENAI_API_KEY")).toBeTruthy();
    const input = await screen.findByLabelText("Message ContextMesh Agent");
    expect((input as HTMLTextAreaElement).disabled).toBe(true);
    expect(
      (
        screen.getByRole("button", {
          name: "Send message",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(fetch.mock.calls.every(([, init]) => init?.method !== "POST")).toBe(
      true,
    );
  });

  it("keeps Shift Enter as a newline and prevents duplicate pending sends", async () => {
    selectConversation();
    let resolve: (response: Response) => void = () => {};
    const response = new Promise<Response>((done) => {
      resolve = done;
    });
    const posts = vi.fn(() => response);
    vi.stubGlobal(
      "fetch",
      createHttp(async (_url, init) => {
        if (init.method === "POST") return posts();
        return emptyHistory();
      }),
    );
    render(<App />);
    const user = userEvent.setup();
    const input = await screen.findByLabelText("Message ContextMesh Agent");
    await waitFor(() =>
      expect((input as HTMLTextAreaElement).disabled).toBe(false),
    );
    await user.type(input, "Help me");
    await user.keyboard("{Shift>}{Enter}{/Shift}plan");
    expect((input as HTMLTextAreaElement).value).toBe("Help me\nplan");
    expect(posts).toHaveBeenCalledTimes(0);
    await user.keyboard("{Enter}");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(posts).toHaveBeenCalledTimes(1);
    expect((input as HTMLTextAreaElement).disabled).toBe(true);
    resolve(json(turn()));
    await screen.findByText("A useful answer.");
  });

  it("starts a persisted conversation before accepting a first message", async () => {
    vi.stubGlobal("fetch", createHttp(emptyHistory));
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "New conversation" }));
    await screen.findByLabelText("Message ContextMesh Agent");
    expect(localStorage.getItem("contextmesh.selected-conversation")).toBe(
      first.id,
    );
    expect(
      screen
        .getByRole("button", { name: "Open conversation: Planning notes" })
        .getAttribute("aria-current"),
    ).toBe("true");
  });
});
