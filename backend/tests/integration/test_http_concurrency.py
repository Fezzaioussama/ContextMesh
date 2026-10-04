"""The HTTP transport stays responsive while a provider execution holds its claim."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from app.bootstrap.api import create_app
from app.domain.models import ModelReply, Usage
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration
ROOT = "/api/v1/assistant"


class WaitingModel:
    configured = True

    def __init__(self):
        self.entered = Event()
        self.release = Event()

    def respond(self, history, message):
        self.entered.set()
        self.release.wait(timeout=5)
        return ModelReply("Finished", Usage(1, 1))


def send(client, conversation_id, key):
    return client.post(
        f"{ROOT}/conversations/{conversation_id}/messages",
        json={"message": "Hello"},
        headers={"Idempotency-Key": key},
    )


def test_blocked_provider_allows_health_checks_and_rejects_concurrent_turn(settings, identity):
    model = WaitingModel()
    with TestClient(create_app(settings, model, identity)) as client:
        conversation_id = client.post(f"{ROOT}/conversations", json={}).json()["id"]
        with ThreadPoolExecutor(max_workers=1) as pool:
            original = pool.submit(send, client, conversation_id, "first")
            model.entered.wait(timeout=5)
            live = client.get("/health/live")
            conflict = send(client, conversation_id, "second")
            history = client.get(f"{ROOT}/conversations/{conversation_id}/messages").json()
            model.release.set()
            finished = original.result(timeout=5)
    assert (live.status_code, conflict.status_code, finished.status_code) == (200, 409, 200)
    assert conflict.json()["error"]["code"] == "turn_in_progress"
    assert len(history["items"]) == 1
