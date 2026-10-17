"""Browser behavior checks with real HTTP traffic, never browser route mocks."""

from urllib.parse import urlparse

from api_checks import verify_agent_calls, verify_failed_agent_calls, verify_single_pair
from playwright.sync_api import expect
from provider_fixture import SECRET
from runtime import DOCUMENT, FACT, QUESTION, request, require

EDITOR = "Message ContextMesh Agent"


def open_app(page, settings):
    page.goto(settings.frontend_url)
    expect(page.get_by_role("heading", name="ContextMesh Agent")).to_be_visible()
    expect(page.locator("header").get_by_text("Agentic retrieval")).to_be_visible()


def send_message(page, message):
    new_conversation(page)
    editor = page.get_by_role("textbox", name=EDITOR)
    editor.fill(message)
    editor.press("Enter")


def new_conversation(page):
    with page.expect_response(is_created_conversation) as created:
        page.get_by_role("button", name="New conversation", exact=True).click()
    conversation_id = created.value.json()["id"]
    selected = page.locator(f"button[data-conversation-id='{conversation_id}']")
    expect(selected).to_have_attribute("aria-current", "true")
    expect(page.get_by_role("textbox", name=EDITOR)).to_be_enabled()


def is_created_conversation(response):
    return response.request.method == "POST" and response.url.endswith("/assistant/conversations")


def is_completed_turn(response):
    return response.request.method == "POST" and response.url.endswith("/messages/stream")


def require_stream(response):
    require(response.status == 200, "Browser stream did not return HTTP 200")
    require(
        response.headers.get("content-type", "").startswith("text/event-stream"),
        "Browser response did not use SSE",
    )


def saved_browser_turn(settings, response):
    """After visible completion, replay the authoritative turn with the browser's key.

    Readers cancel at a terminal event, so Chromium may discard its response body.
    Replay also checks that browser output corresponds to the persisted turn.
    """
    require_stream(response)
    original = response.request
    path = urlparse(response.url).path.removesuffix("/stream")
    status, result, _headers = request(
        settings.api_url, path, original.post_data_json, original.headers["idempotency-key"]
    )
    require(status == 200, f"Browser turn was not available for canonical replay: {result}")
    return result


def verify_browser_sources(page, settings, log_dir):
    """Create a source and upload through the drawer; the real worker publishes it."""
    open_app(page, settings)
    page.get_by_role("button", name="Sources", exact=True).click()
    drawer = page.get_by_role("complementary", name="Knowledge sources")
    drawer.get_by_label("Source name").fill("Browser handbook")
    drawer.get_by_role("button", name="Add source").click()
    upload = drawer.get_by_label("Upload files to Browser handbook")
    upload.set_input_files(
        files=[{"name": "browser.md", "mimeType": "text/markdown", "buffer": DOCUMENT.encode()}]
    )
    card = drawer.locator("li.source-card", has_text="Browser handbook")
    expect(card.get_by_text("Ready · 1 passage")).to_be_visible(timeout=30000)
    page.screenshot(path=str(log_dir / "sources.png"), full_page=True)
    drawer.get_by_role("button", name="Close sources").click()


def verify_browser_chat(page, settings, fixture):
    open_app(page, settings)
    before = len(fixture.requests)
    with page.expect_response(is_completed_turn) as sent:
        send_message(page, QUESTION)
    expect_grounded_answer(page)
    result = saved_browser_turn(settings, sent.value)
    verify_agent_calls(fixture.requests[before:])
    verify_literal_output(page)
    page.reload()
    expect_grounded_answer(page)
    verify_sidebar_history(page, result["conversation_id"])
    return result["conversation_id"]


def expect_grounded_answer(page):
    expect(page.get_by_text("Grounded answer", exact=True).last).to_be_visible()
    expect(page.get_by_role("link", name="Citation 1").last).to_be_visible()
    expect(page.get_by_role("list", name="Citations").last).to_contain_text("handbook")


def verify_literal_output(page):
    require(page.evaluate("window.fixtureExecuted") is None, "Model script executed in browser")
    require(page.locator("b").filter(has_text="literal").count() == 0, "Model HTML rendered markup")
    expect(page.get_by_text(FACT).first).to_be_visible()


def verify_sidebar_history(page, conversation_id):
    new_conversation(page)
    expect(page.get_by_text("Grounded answer", exact=True)).to_have_count(0)
    page.locator(f"button[data-conversation-id='{conversation_id}']").click()
    expect(page.get_by_text("Grounded answer", exact=True).last).to_be_visible()


def verify_ui_failure(page, settings, fixture):
    open_app(page, settings)
    fixture.failures_remaining = 1
    before = len(fixture.requests)
    with page.expect_response(is_completed_turn) as failed:
        send_message(page, QUESTION)
    require_stream(failed.value)
    message = "The model provider could not respond. Your message is saved for retry."
    expect(page.get_by_role("alert").get_by_text(message, exact=True)).to_be_visible()
    expect(page.locator("body")).not_to_contain_text(SECRET)
    expect(page.get_by_role("button", name="Retry message", exact=True)).to_be_visible()
    verify_failed_agent_calls(fixture.requests[before:])
    retry_before = len(fixture.requests)
    retry_message(page, failed.value.request)
    verify_agent_calls(fixture.requests[retry_before:], include_title=False)
    history_path = urlparse(failed.value.url).path.removesuffix("/stream")
    verify_single_pair(settings, history_path)


def retry_message(page, original_request):
    with page.expect_response(is_completed_turn) as retried:
        page.get_by_role("button", name="Retry message", exact=True).click()
    original_key = original_request.headers["idempotency-key"]
    retry_key = retried.value.request.headers["idempotency-key"]
    require(original_key == retry_key, "UI changed idempotency key during recovery")
    require(retried.value.status == 200, "UI retry did not recover saved turn")
    expect_grounded_answer(page)


def verify_live_markdown(page, settings, fixture):
    """Hold the upstream terminal: visible formatted text must precede completion."""
    open_app(page, settings)
    fixture.stream_terminal.clear()
    try:
        with page.expect_response(is_completed_turn) as sent:
            send_message(page, "Show streaming Markdown")
        expect(page.get_by_role("heading", name="Live answer", exact=True)).to_be_visible()
        expect(page.locator("strong").filter(has_text="Streaming works")).to_be_visible()
        expect(page.locator("pre code").filter(has_text="print('hello')")).to_be_visible()
        expect(page.get_by_role("columnheader", name="Feature", exact=True)).to_be_visible()
        expect(
            page.get_by_role("cell", name="Visible before completion", exact=True)
        ).to_be_visible()
        expect(page.get_by_role("button", name="Send message", exact=True)).to_be_disabled()
        expect(page.get_by_text("Direct answer", exact=True)).to_have_count(0)
        require(fixture.tasks("direct_answer")[-1].get("stream") is True, "Provider did not stream")
    finally:
        fixture.stream_terminal.set()
    expect(page.get_by_text("Direct answer", exact=True)).to_be_visible()
    result = saved_browser_turn(settings, sent.value)
    require(
        result["assistant_message"]["answer"]["status"] == "direct", "Wrong direct answer route"
    )
    page.reload()
    expect(page.get_by_role("heading", name="Live answer", exact=True)).to_be_visible()
    page.set_viewport_size({"width": 390, "height": 844})
    try:
        table = page.get_by_role("region", name="Scrollable table", exact=True)
        code = page.get_by_label("Code block", exact=True)
        expect(table).to_be_visible()
        expect(code).to_be_visible()
        table.focus()
        expect(table).to_be_focused()
        code.focus()
        expect(code).to_be_focused()
        dimensions = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
        require(dimensions[0] <= dimensions[1], "Mobile Markdown escapes the viewport")
        require(
            code.evaluate("node => node.scrollWidth > node.clientWidth"),
            "Long code did not scroll locally",
        )
        require(
            table.evaluate("node => node.scrollWidth > node.clientWidth"),
            "Table did not scroll locally",
        )
    finally:
        page.set_viewport_size({"width": 1280, "height": 900})


def verify_mobile(page, settings, log_dir):
    page.set_viewport_size({"width": 390, "height": 844})
    open_app(page, settings)
    page.get_by_role("button", name="Conversations", exact=True).click()
    expect(page.get_by_role("button", name="New conversation", exact=True)).to_be_visible()
    new_conversation(page)
    editor = page.get_by_role("textbox", name=EDITOR)
    editor.fill("First line")
    editor.press("Shift+Enter")
    editor.type("Second line")
    require(editor.input_value() == "First line\nSecond line", "Shift+Enter lost multiline input")
    dimensions = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
    require(dimensions[0] <= dimensions[1], "Mobile layout scrolls horizontally")
    editor.fill("")
    expect(page.get_by_role("button", name="Send message", exact=True)).to_be_disabled()
    page.screenshot(path=str(log_dir / "mobile.png"), full_page=True)
    page.set_viewport_size({"width": 1280, "height": 900})


def verify_ui_missing_configuration(page, settings):
    open_app(page, settings)
    expect(page.get_by_text("Connect your model provider", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Send message", exact=True)).to_be_disabled()
    expect(page.get_by_text("OPENROUTER_API_KEY", exact=True)).to_be_visible()
