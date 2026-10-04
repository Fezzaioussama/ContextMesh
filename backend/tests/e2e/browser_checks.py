"""Browser behavior checks with real HTTP traffic, never browser route mocks."""

import json

from playwright.sync_api import expect
from provider_fixture import REPLY, SECRET
from runtime import require


def open_app(page, settings):
    page.goto(settings.frontend_url)
    expect(page.get_by_role("heading", name="Foundation Assistant")).to_be_visible()
    expect(page.locator("header").get_by_text("knowledge retrieval not connected")).to_be_visible()


def send_message(page, message):
    new_conversation(page)
    editor = page.get_by_role("textbox", name="Message Foundation Assistant")
    editor.fill(message)
    editor.press("Enter")


def new_conversation(page):
    with page.expect_response(is_created_conversation) as created:
        page.get_by_role("button", name="New conversation", exact=True).click()
    conversation_id = created.value.json()["id"]
    selected = page.locator(f"button[data-conversation-id='{conversation_id}']")
    expect(selected).to_have_attribute("aria-current", "true")
    expect(page.get_by_role("textbox", name="Message Foundation Assistant")).to_be_enabled()


def is_created_conversation(response):
    return response.request.method == "POST" and response.url.endswith("/assistant/conversations")


def verify_browser_chat(page, settings, fixture):
    open_app(page, settings)
    before = len(fixture.requests)
    with page.expect_response(is_completed_turn) as sent:
        send_message(page, "Browser persistence smoke")
    result = sent.value.json()
    require(sent.value.status == 200, f"Browser turn failed: {result}")
    expect(page.get_by_text(REPLY, exact=True)).to_be_visible()
    require(len(fixture.requests) == before + 1, "Browser submission duplicated provider calls")
    verify_literal_output(page)
    page.reload()
    expect(page.get_by_text(REPLY, exact=True)).to_be_visible()
    verify_sidebar_history(page, result["conversation_id"])
    return result["conversation_id"]


def is_completed_turn(response):
    return response.request.method == "POST" and response.url.endswith("/messages")


def verify_literal_output(page):
    require(page.evaluate("window.fixtureExecuted") is None, "Model script executed in browser")
    require(page.locator("b").filter(has_text="literal").count() == 0, "Model HTML rendered markup")


def verify_sidebar_history(page, conversation_id):
    new_conversation(page)
    expect(page.get_by_text(REPLY, exact=True)).to_have_count(0)
    page.locator(f"button[data-conversation-id='{conversation_id}']").click()
    expect(page.get_by_text(REPLY, exact=True)).to_be_visible()


def verify_ui_failure(page, settings, fixture):
    open_app(page, settings)
    fixture.failures_remaining = 1
    before = len(fixture.requests)
    with page.expect_response(is_completed_turn) as failed:
        send_message(page, "Browser safe retry smoke")
    require(failed.value.status == 503, "Browser provider failure was not 503")
    require(SECRET not in json.dumps(failed.value.json()), "Browser API exposed provider detail")
    expect(page.get_by_role("button", name="Retry message", exact=True)).to_be_visible()
    require(len(fixture.requests) == before + 1, "Browser failure silently retried provider")
    retry_message(page, failed.value.request)
    require(len(fixture.requests) == before + 2, "Explicit retry did not make one model call")


def retry_message(page, original_request):
    with page.expect_response(is_completed_turn) as retried:
        page.get_by_role("button", name="Retry message", exact=True).click()
    original_key = original_request.headers["idempotency-key"]
    retry_key = retried.value.request.headers["idempotency-key"]
    require(original_key == retry_key, "UI changed idempotency key during recovery")
    require(retried.value.status == 200, "UI retry did not recover saved turn")
    expect(page.get_by_text(REPLY, exact=True)).to_be_visible()


def verify_mobile(page, settings, log_dir):
    page.set_viewport_size({"width": 390, "height": 844})
    open_app(page, settings)
    page.get_by_role("button", name="Conversations", exact=True).click()
    expect(page.get_by_role("button", name="New conversation", exact=True)).to_be_visible()
    new_conversation(page)
    editor = page.get_by_role("textbox", name="Message Foundation Assistant")
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
    verify_provider_setup(page, settings.model_provider)


def verify_provider_setup(page, provider):
    key_name = "OPENAI_API_KEY"
    if provider == "openrouter":
        key_name = "OPENROUTER_API_KEY"
    expect(page.get_by_text(key_name, exact=True)).to_be_visible()
