"""Real HTTP, persistence, composition, and SDK using a controlled OpenRouter transport."""

import json
from uuid import uuid4

import httpx
from app.ai.llm import openai as openai_chat
from app.bootstrap.api import create_app
from app.core.config import Settings
from fastapi.testclient import TestClient
from openai import OpenAI


class OpenRouterTransport:
    def __init__(self):
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "resp_fixture",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "vendor/fixture-model",
                "output": [
                    {
                        "id": "msg_fixture",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Router fixture reply",
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
            },
        )


def install_transport(monkeypatch, transport):
    def sdk_client(**kwargs):
        return OpenAI(http_client=httpx.Client(transport=httpx.MockTransport(transport)), **kwargs)

    monkeypatch.setattr(openai_chat, "OpenAI", sdk_client)


def send(client, path, text):
    response = client.post(path, json={"message": text}, headers={"Idempotency-Key": str(uuid4())})
    assert response.status_code == 200
    return response.json()


def test_openrouter_metadata_and_follow_up_use_selected_sdk_settings(
    database_url, identity, monkeypatch
):
    transport = OpenRouterTransport()
    install_transport(monkeypatch, transport)
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        model_provider="openrouter",
        api_key="unused-direct-key",
        openrouter_api_key="router-fixture-key",
        openrouter_model="vendor/fixture-model",
    )
    with TestClient(create_app(settings, identity=identity)) as client:
        assert_metadata(client)
        conversation = client.post("/api/v1/assistant/conversations", json={}).json()
        path = f"/api/v1/assistant/conversations/{conversation['id']}/messages"
        first = send(client, path, "First")
        send(client, path, "Follow up")
    assert_routing(transport)
    assert_history(transport, first)


def assert_metadata(client):
    response = client.get("/api/v1/assistant")
    metadata = response.json()
    assert (metadata["provider"], metadata["model"], metadata["configured"]) == (
        "openrouter",
        "vendor/fixture-model",
        True,
    )
    assert "router-fixture-key" not in response.text
    assert "unused-direct-key" not in response.text


def assert_routing(transport):
    assert len(transport.requests) == 2
    request = transport.requests[-1]
    assert (str(request.url), request.headers["Authorization"]) == (
        "https://openrouter.ai/api/v1/responses",
        "Bearer router-fixture-key",
    )


def assert_history(transport, first):
    payload = json.loads(transport.requests[-1].content)
    assert (
        payload["model"],
        payload["store"],
        payload["max_output_tokens"],
        payload.get("tools"),
    ) == ("vendor/fixture-model", False, 1024, None)
    saved = payload["input"][1]
    message_id = first["assistant_message"]["id"].replace("-", "")
    assert (
        saved["id"],
        saved["status"],
        saved["content"][0]["type"],
        saved["content"][0]["text"],
    ) == (f"msg_{message_id}", "completed", "output_text", "Router fixture reply")


def test_openrouter_missing_key_never_falls_back_or_calls_provider(
    database_url, identity, monkeypatch
):
    transport = OpenRouterTransport()
    install_transport(monkeypatch, transport)
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        model_provider="openrouter",
        api_key="unused-direct-key",
        openrouter_api_key="",
    )
    with TestClient(create_app(settings, identity=identity)) as client:
        assert client.get("/api/v1/assistant").json()["configured"] is False
        conversation = client.post("/api/v1/assistant/conversations", json={}).json()
        path = f"/api/v1/assistant/conversations/{conversation['id']}/messages"
        response = client.post(
            path, json={"message": "Hello"}, headers={"Idempotency-Key": "no-key"}
        )
        assert (response.status_code, response.json()["error"]["code"]) == (
            503,
            "provider_not_configured",
        )
    assert transport.requests == []
