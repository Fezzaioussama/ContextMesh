"""Public transport guarantees exercised against the real API/provider adapter."""

import json
from uuid import uuid4

from provider_fixture import REPLY, SECRET
from runtime import request, require


def create_conversation(settings):
    status, body, headers = request(
        settings.api_url, "/api/v1/assistant/conversations", {"title": "API smoke"}
    )
    require(status == 201, f"Conversation creation failed: {status} {body}")
    require(bool(headers.get("X-Request-ID")), "Missing safe request tracing header")
    return body["id"]


def completed_turn(settings, conversation_id, key, message):
    path = f"/api/v1/assistant/conversations/{conversation_id}/messages"
    status, body, _headers = request(settings.api_url, path, {"message": message}, key)
    require(status == 200, f"Turn failed: {status} {body}")
    require(body["assistant_message"]["content"] == REPLY, "Wrong real-adapter reply")
    return body


def verify_replay(settings, fixture):
    conversation_id = create_conversation(settings)
    key = str(uuid4())
    before = len(fixture.requests)
    first = completed_turn(settings, conversation_id, key, "Replay this response")
    replay = completed_turn(settings, conversation_id, key, "Replay this response")
    require(first == replay, "Idempotent replay did not return the saved result")
    require(len(fixture.requests) == before + 1, "Replay made another provider request")
    verify_conflict(settings, conversation_id, key)
    verify_provider_bounds(fixture.requests[-1])
    verify_saved_context(settings, fixture, conversation_id)


def verify_conflict(settings, conversation_id, key):
    path = f"/api/v1/assistant/conversations/{conversation_id}/messages"
    status, body, _headers = request(settings.api_url, path, {"message": "Different"}, key)
    require(status == 409, "Payload change did not conflict")
    require(body["error"]["code"] == "idempotency_conflict", "Wrong conflict error")


def verify_provider_bounds(payload):
    require(payload["store"] is False, "Responses storage must be disabled")
    require(payload["max_output_tokens"] <= 1024, "Provider output budget exceeded")
    require(not payload.get("tools"), "Initial assistant unexpectedly enabled tools")


def verify_saved_context(settings, fixture, conversation_id):
    completed_turn(settings, conversation_id, str(uuid4()), "Use the saved conversation")
    transmitted = json.dumps(fixture.requests[-1]["input"])
    require("Replay this response" in transmitted, "Prior user context omitted from provider call")
    require(REPLY in transmitted, "Prior assistant context omitted from provider call")
    verify_assistant_item(fixture.requests[-1]["input"][1])


def verify_assistant_item(item):
    require(item["role"] == "assistant", "Saved assistant message was not replayed in order")
    require(bool(item["id"]), "Saved assistant input is missing its stable message ID")
    require(item["status"] == "completed", "Saved assistant input is missing completed status")


def verify_safe_failure(settings, fixture):
    conversation_id = create_conversation(settings)
    fixture.failures_remaining = 1
    before = len(fixture.requests)
    path = f"/api/v1/assistant/conversations/{conversation_id}/messages"
    key = str(uuid4())
    status, body, _headers = request(settings.api_url, path, {"message": "Recover me"}, key)
    require(status == 503, f"Provider failure was not safe 503: {status}")
    require(SECRET not in json.dumps(body), "Raw provider diagnostic leaked")
    require(len(fixture.requests) == before + 1, "SDK unexpectedly retried the provider")
    completed_turn(settings, conversation_id, key, "Recover me")
    verify_single_pair(settings, path)


def verify_single_pair(settings, path):
    status, messages, _headers = request(settings.api_url, path)
    require(status == 200, "Recovered messages were unavailable")
    require(len(messages["items"]) == 2, "Provider retry duplicated the user message")


def verify_history(settings, conversation_id):
    path = f"/api/v1/assistant/conversations/{conversation_id}/messages"
    status, body, _headers = request(settings.api_url, path)
    require(status == 200, "Persisted conversation missing after API restart")
    require(body["items"][-1]["content"] == REPLY, "Persisted reply changed after restart")


def verify_missing_configuration(settings, fixture):
    conversation_id = create_conversation(settings)
    before = len(fixture.requests)
    path = f"/api/v1/assistant/conversations/{conversation_id}/messages"
    status, body, _headers = request(settings.api_url, path, {"message": "Hello"}, str(uuid4()))
    require(status == 503, "Missing model credentials did not return 503")
    require(body["error"]["code"] == "provider_not_configured", "Wrong configuration error")
    require(len(fixture.requests) == before, "Missing credentials attempted a provider call")
