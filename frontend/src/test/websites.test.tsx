import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { App } from "../App";
import type { Answer, Job, Source } from "../api/contracts";
import { webUrl } from "../components/ExternalLink";
import {
  createHttp,
  groundedAnswer,
  handbook,
  indexJob,
  json,
  message,
  selectConversation,
} from "./fixtures";

const docsSite: Source = {
  ...handbook,
  id: "4b3c9e1a-5a8d-4f63-9f1e-2c7a0b9d1e22",
  kind: "website",
  name: "Product docs",
  url: "https://docs.example.com/guide/",
};

function openSources() {
  fireEvent.click(screen.getByRole("button", { name: "Sources" }));
}

describe("website sources", () => {
  it("registers the start URL and starts a crawl", async () => {
    const posts: { url: string; body: unknown }[] = [];
    vi.stubGlobal(
      "fetch",
      createHttp(async (url, init) => {
        if (init.method !== "POST") return json({ items: [] });
        posts.push({ url, body: init.body ? JSON.parse(String(init.body)) : null });
        return url.endsWith("/sync") ? json({ job_id: "crawl" }, 202) : json(docsSite, 201);
      }),
    );
    render(<App />);
    openSources();
    fireEvent.click(screen.getByLabelText("Website"));
    fireEvent.change(screen.getByLabelText("Source name"), { target: { value: "Product docs" } });
    fireEvent.change(screen.getByLabelText(/Start page URL/), {
      target: { value: "https://docs.example.com/guide/" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add source" }));
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts.map((post) => post.url)).toEqual([
      "/api/v1/sources",
      `/api/v1/sources/${docsSite.id}/sync`,
    ]);
    expect(posts[0].body).toEqual({
      name: "Product docs",
      description: "",
      url: "https://docs.example.com/guide/",
    });
  });

  it("shows crawl progress and the reason a crawl failed", async () => {
    const crawling = { ...docsSite, latest_sync: indexJob({ kind: "source.sync_requested", status: "running" }) };
    const failed = {
      ...docsSite,
      id: "f0e1d2c3-b4a5-4968-8776-655443322110",
      name: "Blog",
      latest_sync: indexJob({ status: "failed", error_code: "blocked_destination" }),
    };
    vi.stubGlobal("fetch", createHttp(async () => json({ items: [] }), true, [crawling, failed]));
    render(<App />);
    openSources();
    expect(await screen.findByText("Crawling the site…")).toBeTruthy();
    expect(
      screen.getByText("Crawl failed: the address is private or not allowed"),
    ).toBeTruthy();
    const buttons = screen.getAllByRole("button", { name: "Crawl again" });
    expect(buttons.map((button) => (button as HTMLButtonElement).disabled)).toEqual([true, false]);
  });

  it("tells partial and cancelled crawls apart from complete ones", async () => {
    const ended = (id: string, name: string, overrides: Partial<Job>): Source => ({
      ...docsSite,
      id,
      name,
      latest_sync: indexJob({ kind: "source.sync_requested", ...overrides }),
    });
    const sites = [
      ended("0b6f9a54-2f0e-4c39-9f43-6f1b7e2a0c11", "Docs", { stage: "crawled" }),
      ended("1c7a0b65-3a1f-4d4a-8a54-7a2c8f3b1d22", "Blog", { stage: "crawled_partial" }),
      ended("2d8b1c76-4b2a-4e5b-9b65-8b3d9a4c2e33", "Wiki", { status: "cancelled", stage: null }),
    ];
    vi.stubGlobal("fetch", createHttp(async () => json({ items: [] }), true, sites));
    render(<App />);
    openSources();
    expect(await screen.findByText("Crawl finished")).toBeTruthy();
    expect(
      screen.getByText("Partly crawled: some pages could not be read, so none were removed"),
    ).toBeTruthy();
    expect(screen.getByText("Crawl cancelled")).toBeTruthy();
  });
});

describe("web and page citations", () => {
  it("shows the page number and a safe link to the source page", async () => {
    selectConversation();
    const cited: Answer = {
      ...groundedAnswer,
      citations: [
        {
          ...groundedAnswer.citations[0],
          title: "Install",
          locator: { heading_path: ["Install"], line_start: 1, line_end: 1, page: 2, slide: null },
          source_url: "https://docs.example.com/guide/install.html",
        },
      ],
    };
    vi.stubGlobal(
      "fetch",
      createHttp(async () =>
        json({ items: [message("a", "assistant", "Run it. [1]", cited)], next_cursor: null }),
      ),
    );
    render(<App />);
    const link = await screen.findByRole("link", { name: "Open page ↗" });
    expect(link.getAttribute("href")).toBe("https://docs.example.com/guide/install.html");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(screen.getByText("Install · page 2")).toBeTruthy();
  });

  it("never turns non-web URLs into links", () => {
    expect([
      webUrl("javascript:alert(1)"),
      webUrl("data:text/html,x"),
      webUrl("not a url"),
      webUrl("https://example.com/a"),
    ]).toEqual([null, null, null, "https://example.com/a"]);
  });
});
