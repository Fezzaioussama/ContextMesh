"""Public contract, canonical persistence and identity isolation over real PostgreSQL."""

from uuid import uuid4

import pytest
from app.bootstrap.api import create_app
from app.core.security import Identity
from app.db.models.user import memberships
from app.domain.errors import provider_unavailable
from fastapi.testclient import TestClient
from sqlalchemy import delete

pytestmark = pytest.mark.integration
ROOT = "/api/v1/assistant"


def send(client, conversation_id, message="Hello", key="turn-key"):
    return client.post(
        f"{ROOT}/conversations/{conversation_id}/messages",
        json={"message": message},
        headers={"Idempotency-Key": key},
    )


def test_metadata_and_health(client):
    metadata = client.get(ROOT).json()
    assert (metadata["configured"], metadata["mode"], metadata["retrieval_enabled"]) == (
        True,
        "provider_chat",
        False,
    )
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {"status": "ready"}


def test_send_persists_context_and_replay_without_duplicate_model_call(
    client, conversation_id, model
):
    first = send(client, conversation_id)
    replay = send(client, conversation_id, "  Hello  ")
    assert replay.json() == first.json()
    assert len(model.calls) == 1
    assert client.get(f"{ROOT}/conversations/{conversation_id}/messages").json()["items"] == [
        first.json()["user_message"],
        first.json()["assistant_message"],
    ]


def test_saved_context_is_sent_on_subsequent_turn(client, conversation_id, model):
    send(client, conversation_id)
    send(client, conversation_id, "Follow-up", "next-key")
    history, message = model.calls[-1]
    assert [item.content for item in history] == ["Hello", "Fixture reply"]
    assert message == "Follow-up"


def test_key_conflict_and_safe_request_id(client, conversation_id):
    send(client, conversation_id)
    conflict = send(client, conversation_id, "Different")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    assert conflict.json()["error"]["request_id"] == conflict.headers["X-Request-ID"]


@pytest.mark.parametrize(
    "body",
    [
        {"message": "   "},
        {"message": "x" * 8001},
        {"message": "Hello", "workspace_id": str(uuid4())},
        {"message": 123},
    ],
)
def test_strict_body_validation(client, conversation_id, body):
    response = client.post(
        f"{ROOT}/conversations/{conversation_id}/messages",
        json=body,
        headers={"Idempotency-Key": "key"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"


def test_idempotency_header_is_required(client, conversation_id):
    response = client.post(
        f"{ROOT}/conversations/{conversation_id}/messages", json={"message": "Hi"}
    )
    assert response.status_code == 422


def test_missing_provider_is_safe_and_does_not_fake_reply(client, conversation_id, model):
    model.configured = False
    response = send(client, conversation_id)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "provider_not_configured"
    assert model.calls == []


def test_completed_replay_survives_provider_key_removal(client, conversation_id, model):
    first = send(client, conversation_id)
    model.configured = False
    replay = send(client, conversation_id)
    assert replay.json() == first.json()
    assert len(model.calls) == 1


def test_provider_failure_retry_reuses_saved_user_message(client, conversation_id, model):
    model.failure = provider_unavailable()
    failed = send(client, conversation_id)
    model.failure = None
    retry = send(client, conversation_id)
    assert failed.status_code == 503
    assert retry.status_code == 200
    assert len(client.get(f"{ROOT}/conversations/{conversation_id}/messages").json()["items"]) == 2


def test_history_survives_api_restart(settings, identity, model, client, conversation_id):
    first = send(client, conversation_id)
    with TestClient(create_app(settings, model, identity)) as restarted:
        history = restarted.get(f"{ROOT}/conversations/{conversation_id}/messages").json()["items"]
    assert history == [first.json()["user_message"], first.json()["assistant_message"]]


@pytest.mark.parametrize("dimension", ["subject", "workspace", "both"])
def test_other_requester_and_missing_ids_are_indistinguishable(
    settings, model, client, conversation_id, identity, dimension
):
    send(client, conversation_id)
    others = {
        "subject": Identity(f"other-{uuid4()}", identity.workspace_id),
        "workspace": Identity(identity.subject, uuid4()),
        "both": Identity(f"other-{uuid4()}", uuid4()),
    }
    with TestClient(create_app(settings, model, others[dimension])) as unauthorized:
        inaccessible = send(unauthorized, conversation_id)
        missing = send(unauthorized, uuid4())
        history = unauthorized.get(f"{ROOT}/conversations/{conversation_id}/messages")
        listed = unauthorized.get(f"{ROOT}/conversations").json()
    assert (inaccessible.status_code, missing.status_code, history.status_code) == (404, 404, 404)
    assert inaccessible.json()["error"]["message"] == missing.json()["error"]["message"]
    assert listed["items"] == []


def test_client_identity_headers_do_not_override_server_scope(client, conversation_id):
    response = client.get(
        f"{ROOT}/conversations/{conversation_id}/messages",
        headers={"X-User-ID": "intruder", "X-Workspace-ID": str(uuid4())},
    )
    assert response.status_code == 200


def test_revoked_membership_blocks_history_and_saved_replay(
    client, conversation_id, engine, identity
):
    send(client, conversation_id)
    with engine.begin() as connection:
        connection.execute(
            delete(memberships).where(
                memberships.c.workspace_id == identity.workspace_id,
                memberships.c.subject == identity.subject,
            )
        )
    assert send(client, conversation_id).status_code == 404
    assert client.get(f"{ROOT}/conversations/{conversation_id}/messages").status_code == 404
