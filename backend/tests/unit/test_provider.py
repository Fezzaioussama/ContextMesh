"""Use the actual SDK against a deterministic HTTP transport, without credentials."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from app.ai.llm.openai import OpenAIChatModel
from app.core.exceptions import ContextMeshError
from app.domain.models import Message, Usage
from openai import OpenAI


def response_body(status="completed", content="Hello"):
    return {
        "id": "resp_fixture",
        "object": "response",
        "created_at": 1,
        "status": status,
        "model": "fixture-model",
        "output": [
            {
                "id": "msg_fixture",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": content, "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 4,
            "total_tokens": 14,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


def model_with_transport(handler):
    client = OpenAI(
        api_key="test-only",
        base_url="http://provider.test/v1",
        max_retries=0,
        timeout=45,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return OpenAIChatModel(
        api_key="test-only",
        model="fixture-model",
        base_url="http://provider.test/v1",
        timeout=45,
        max_output_tokens=1024,
        client=client,
    )


class RecordingProvider:
    def __init__(self, status_code=200, body=None):
        self.requests = []
        self.status_code = status_code
        self.body = response_body()
        if body is not None:
            self.body = body

    def __call__(self, request):
        self.requests.append(json.loads(request.content))
        return httpx.Response(self.status_code, json=self.body)


def test_sdk_sends_scoped_history_without_stored_state_or_tools():
    provider = RecordingProvider()
    model = model_with_transport(provider)
    history = (Message(uuid4(), uuid4(), "user", "Earlier", datetime.now(UTC)),)
    model.respond(history, "Now")
    request = provider.requests[0]
    assert request["input"] == [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Earlier"}]},
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Now"}]},
    ]
    assert (request["store"], request["max_output_tokens"], request.get("tools")) == (
        False,
        1024,
        None,
    )
    model.close()


def test_sdk_replays_saved_assistant_as_completed_output_message():
    provider = RecordingProvider()
    model = model_with_transport(provider)
    saved = Message(uuid4(), uuid4(), "assistant", "Earlier reply", datetime.now(UTC))
    model.respond((saved,), "Follow up")
    assert provider.requests[0]["input"][0] == {
        "id": f"msg_{saved.id.hex}",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": "Earlier reply", "annotations": []}],
    }
    model.close()


def test_sdk_maps_response_text_and_actual_usage():
    model = model_with_transport(RecordingProvider())
    reply = model.respond((), "Hello")
    assert reply.content == "Hello"
    assert reply.usage == Usage(10, 4)
    model.close()


@pytest.mark.parametrize("status_code", [401, 429, 500])
def test_sdk_does_not_retry_or_leak_provider_errors(status_code):
    provider = RecordingProvider(status_code, {"error": {"message": "secret prompt/key fixture"}})
    model = model_with_transport(provider)
    with pytest.raises(ContextMeshError) as error:
        model.respond((), "sensitive user prompt")
    assert len(provider.requests) == 1
    assert (error.value.code, error.value.message) == (
        "provider_unavailable",
        "The model provider is unavailable. Retry later.",
    )
    model.close()


@pytest.mark.parametrize("body", [response_body("incomplete"), response_body(content="")])
def test_incomplete_or_empty_model_output_is_a_safe_failure(body):
    model = model_with_transport(RecordingProvider(body=body))
    with pytest.raises(ContextMeshError, match="provider is unavailable"):
        model.respond((), "Hello")
    model.close()


def test_missing_provider_never_constructs_sdk_or_falls_back():
    model = OpenAIChatModel(
        api_key="",
        model="gpt-4.1-mini",
        base_url="http://unused",
        timeout=45,
        max_output_tokens=1024,
    )
    with pytest.raises(ContextMeshError) as error:
        model.respond((), "Hello")
    assert model.client is None
    assert error.value.code == "provider_not_configured"


class TimeoutProvider:
    def __init__(self):
        self.timeout = None
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        self.timeout = request.extensions["timeout"]
        raise httpx.ReadTimeout("private transport details", request=request)


def test_sdk_timeout_is_bounded_and_failure_is_safe_without_retry():
    provider = TimeoutProvider()
    model = model_with_transport(provider)
    with pytest.raises(ContextMeshError) as error:
        model.respond((), "Hello")
    assert provider.timeout == {"connect": 45, "read": 45, "write": 45, "pool": 45}
    assert provider.calls == 1
    assert error.value.code == "provider_unavailable"
    model.close()
