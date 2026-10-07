"""Real composition and SDK adapters for OpenRouter, through a controlled HTTP transport."""

import json
from contextlib import ExitStack
from uuid import uuid4

import httpx
import pytest
from app.ai.llm import embeddings as embedding_adapter
from app.ai.llm import openai as reasoning_adapter
from app.bootstrap import services as service_factory
from app.bootstrap.api import create_app
from app.bootstrap.knowledge import ingestion_worker
from app.bootstrap.services import create_services
from app.core.config import Settings
from fastapi.testclient import TestClient
from openai import OpenAI
from qdrant_client import QdrantClient
from support import DEFAULT_SCRIPT, HashEmbeddings, ask, create_source, drain, upload

pytestmark = pytest.mark.integration


class OpenRouterTransport:
    def __init__(self):
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        payload = json.loads(request.content)
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json=_embedding_body(payload["input"]))
        return httpx.Response(200, json=_response_body(payload))


def _embedding_body(texts):
    vectors = HashEmbeddings().embed(texts)
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": index, "embedding": list(vector)}
            for index, vector in enumerate(vectors)
        ],
        "model": "vendor/embed",
        "usage": {"prompt_tokens": 1, "total_tokens": 1},
    }


def _response_body(payload):
    task = payload["text"]["format"]["name"]
    content = json.loads(payload["input"][0]["content"][0]["text"])
    text = json.dumps(DEFAULT_SCRIPT[task](content))
    return {
        "id": "resp_fixture",
        "object": "response",
        "created_at": 1,
        "status": "completed",
        "model": payload["model"],
        "output": [
            {
                "id": "msg_fixture",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
    }


@pytest.fixture
def transport(monkeypatch):
    recorder = OpenRouterTransport()

    def sdk_client(**kwargs):
        return OpenAI(http_client=httpx.Client(transport=httpx.MockTransport(recorder)), **kwargs)

    monkeypatch.setattr(reasoning_adapter, "OpenAI", sdk_client)
    monkeypatch.setattr(embedding_adapter, "OpenAI", sdk_client)
    monkeypatch.setattr(service_factory, "QdrantClient", lambda **_: QdrantClient(":memory:"))
    return recorder


def router_settings(database_url, tmp_path, key="router-fixture-key"):
    return Settings(
        _env_file=None,
        database_url=database_url,
        blob_dir=tmp_path,
        model_provider="openrouter",
        api_key="unused-direct-key",
        openrouter_api_key=key,
        openrouter_model="vendor/fixture-model",
        openrouter_embedding_model="vendor/embed",
    )


def test_openrouter_indexes_and_answers_with_selected_settings(
    database_url, engine, identity, tmp_path, transport
):
    settings = router_settings(database_url, tmp_path)
    with ExitStack() as resources:
        services = create_services(settings, resources)
        with TestClient(create_app(settings, services, identity)) as client:
            metadata = client.get("/api/v1/assistant")
            upload(client, create_source(client), "auth.md", "# Auth\n\nWe use OIDC.")
            drain(ingestion_worker(engine, settings, services))
            conversation = client.post("/api/v1/assistant/conversations", json={}).json()["id"]
            answer = ask(client, conversation, "Which protocol?").json()
    assert (metadata.json()["provider"], metadata.json()["embedding_model"]) == (
        "openrouter",
        "vendor/embed",
    )
    assert "router-fixture-key" not in metadata.text and "unused-direct-key" not in metadata.text
    assert answer["assistant_message"]["answer"]["status"] == "answered"
    assert_routing(transport, settings)


def assert_routing(transport, settings):
    routes = {
        (request.url.host, request.url.path, request.headers["Authorization"])
        for request in transport.requests
    }
    assert routes == {
        ("openrouter.ai", "/api/v1/embeddings", "Bearer router-fixture-key"),
        ("openrouter.ai", "/api/v1/responses", "Bearer router-fixture-key"),
    }
    assert sent_shapes(transport) == {
        ("embeddings", "vendor/embed", None, None, None),
        ("responses", "vendor/fixture-model", False, settings.max_output_tokens, True),
    }


def sent_shapes(transport):
    """Each distinct request shape: endpoint, model, storage, output budget, strictness."""
    return {_shape(request) for request in transport.requests}


def _shape(request):
    payload = json.loads(request.content)
    strict = payload.get("text", {}).get("format", {}).get("strict")
    endpoint = request.url.path.rsplit("/", 1)[-1]
    return (
        endpoint,
        payload["model"],
        payload.get("store"),
        payload.get("max_output_tokens"),
        strict,
    )


def test_openrouter_missing_key_never_falls_back_or_calls_provider(
    database_url, identity, tmp_path, transport
):
    settings = router_settings(database_url, tmp_path, key="")
    with ExitStack() as resources:
        services = create_services(settings, resources)
        with TestClient(create_app(settings, services, identity)) as client:
            configured = client.get("/api/v1/assistant").json()["configured"]
            conversation = client.post("/api/v1/assistant/conversations", json={}).json()["id"]
            response = ask(client, conversation, "Hello", str(uuid4()))
    assert configured is False
    assert (response.status_code, response.json()["error"]["code"]) == (
        503,
        "provider_not_configured",
    )
    assert transport.requests == []
