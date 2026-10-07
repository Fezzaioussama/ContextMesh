"""Public transport guarantees exercised against the real API, worker, and adapters."""

import json
import time
from uuid import uuid4

from provider_fixture import SECRET, task_content
from runtime import ANSWER, DOCUMENT, FACT, QUESTION, request, require, upload


def create_conversation(settings):
    status, body, headers = request(
        settings.api_url, "/api/v1/assistant/conversations", {"title": "API smoke"}
    )
    require(status == 201, f"Conversation creation failed: {status} {body}")
    require(bool(headers.get("X-Request-ID")), "Missing safe request tracing header")
    return body["id"]


def index_fixture_document(settings, name="Smoke handbook"):
    status, source, _headers = request(
        settings.api_url, "/api/v1/sources", {"name": name, "description": "Fixture facts"}
    )
    require(status == 201, f"Source creation failed: {status} {source}")
    status, receipt, _headers = upload(settings.api_url, source["id"], "handbook.md", DOCUMENT)
    require(status == 202, f"Upload was not accepted: {status} {receipt}")
    wait_searchable(settings, source["id"])
    return source["id"]


def wait_searchable(settings, source_id):
    deadline = time.monotonic() + 40
    path = f"/api/v1/sources/{source_id}/documents"
    while time.monotonic() < deadline:
        _status, body, _headers = request(settings.api_url, path)
        if body["items"] and body["items"][0]["searchable"]:
            return
        time.sleep(0.25)
    raise TimeoutError(f"The worker did not publish the fixture document: {body}")


def completed_turn(settings, conversation_id, key, message):
    path = f"/api/v1/assistant/conversations/{conversation_id}/messages"
    status, body, _headers = request(settings.api_url, path, {"message": message}, key)
    require(status == 200, f"Turn failed: {status} {body}")
    require(body["assistant_message"]["content"] == ANSWER, "Wrong grounded answer")
    verify_citation(settings, body["assistant_message"]["answer"])
    return body


def verify_citation(settings, answer):
    require(answer["status"] == "answered", f"Answer was not grounded: {answer}")
    citation = answer["citations"][0]
    require(citation["locator"]["heading_path"] == ["Fixture handbook", "Facts"], "Bad locator")
    status, evidence, _headers = request(settings.api_url, citation["evidence_path"])
    require(status == 200 and evidence["text"] == FACT, "Citation did not resolve to evidence")


def verify_replay(settings, fixture):
    conversation_id = create_conversation(settings)
    key = str(uuid4())
    before = len(fixture.requests)
    first = completed_turn(settings, conversation_id, key, QUESTION)
    calls = len(fixture.requests)
    replay = completed_turn(settings, conversation_id, key, QUESTION)
    require(first == replay, "Idempotent replay did not return the saved result")
    require(len(fixture.requests) == calls, "Replay made another provider request")
    require(calls - before == 4, f"Expected four bounded agent calls, saw {calls - before}")
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
    require(payload["max_output_tokens"] <= 16384, "Provider output budget exceeded")
    require(not payload.get("tools"), "Agent unexpectedly enabled provider tools")
    require(payload["text"]["format"]["strict"] is True, "Structured output is not strict")


def verify_saved_context(settings, fixture, conversation_id):
    completed_turn(settings, conversation_id, str(uuid4()), "And which fixture fact again?")
    conversation = task_content(fixture.tasks("search_plan")[-1])["conversation"]
    contents = [item["content"] for item in conversation]
    require(QUESTION in contents, "Prior user turn omitted from planning context")
    require(ANSWER in contents, "Prior grounded answer omitted from planning context")


def verify_safe_failure(settings, fixture):
    conversation_id = create_conversation(settings)
    fixture.failures_remaining = 1
    before = len(fixture.requests)
    path = f"/api/v1/assistant/conversations/{conversation_id}/messages"
    key = str(uuid4())
    status, body, _headers = request(settings.api_url, path, {"message": QUESTION}, key)
    require(status == 503, f"Provider failure was not safe 503: {status}")
    require(SECRET not in json.dumps(body), "Raw provider diagnostic leaked")
    require(len(fixture.requests) == before + 1, "SDK unexpectedly retried the provider")
    completed_turn(settings, conversation_id, key, QUESTION)
    verify_single_pair(settings, path)


def verify_single_pair(settings, path):
    status, messages, _headers = request(settings.api_url, path)
    require(status == 200, "Recovered messages were unavailable")
    require(len(messages["items"]) == 2, "Provider retry duplicated the user message")


def verify_history(settings, conversation_id):
    path = f"/api/v1/assistant/conversations/{conversation_id}/messages"
    status, body, _headers = request(settings.api_url, path)
    require(status == 200, "Persisted conversation missing after API restart")
    saved = body["items"][-1]
    require(saved["content"] == ANSWER, "Persisted answer changed after restart")
    require(saved["answer"]["citations"], "Persisted citations missing after restart")


def verify_missing_configuration(settings, fixture):
    conversation_id = create_conversation(settings)
    before = len(fixture.requests)
    path = f"/api/v1/assistant/conversations/{conversation_id}/messages"
    status, body, _headers = request(settings.api_url, path, {"message": "Hello"}, str(uuid4()))
    require(status == 503, "Missing model credentials did not return 503")
    require(body["error"]["code"] == "provider_not_configured", "Wrong configuration error")
    require(len(fixture.requests) == before, "Missing credentials attempted a provider call")
