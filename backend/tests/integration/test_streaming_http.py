"""SSE follows the canonical PostgreSQL publication and idempotency contracts."""

import json
from uuid import uuid4

from app.utils.errors import ContextMeshError
from support import create_source, drain, upload


def send(client, conversation_id, key, **body):
    return client.post(
        f"/api/v1/assistant/conversations/{conversation_id}/messages/stream",
        json={"message": "What is the rollout policy?", **body},
        headers={"Idempotency-Key": key},
    )


def frames(response):
    assert response.status_code == 200, response.text
    return [
        (name.removeprefix("event: "), json.loads(data.removeprefix("data: ")))
        for name, data in (frame.split("\n", 1) for frame in response.text.strip().split("\n\n"))
    ]


def test_grounded_stream_replay_withholds_deleted_evidence(
    client, worker, conversation_id, reasoning
):
    source = create_source(client)
    upload(client, source, "policy.md", "# Policy\n\nThe rollout begins on Monday.")
    drain(worker)
    key = str(uuid4())
    first = frames(send(client, conversation_id, key, source_ids=[source]))
    assert first[0][0] == "progress"
    assert first[-1][0] == "completed"
    saved = first[-1][1]
    answer = saved["assistant_message"]
    assert answer["answer"]["status"] == "answered"
    assert [data["text"] for event, data in first if event == "delta"] == [answer["content"]]
    assert any(data["stage"] == "verify" for event, data in first if event == "progress")
    calls = len(reasoning.tasks)
    assert frames(send(client, conversation_id, key, source_ids=[source]))[-1][1] == saved
    assert len(reasoning.tasks) == calls
    assert client.delete(f"/api/v1/sources/{source}").status_code == 202
    withheld = frames(send(client, conversation_id, key, source_ids=[source]))[-1][1]
    assert withheld["assistant_message"]["answer"]["status"] == "withheld"
    assert "Monday" not in withheld["assistant_message"]["content"]
    assert len(reasoning.tasks) == calls


def test_stream_failure_retries_same_key_without_duplicate_user_message(
    client, worker, conversation_id, reasoning
):
    source = create_source(client)
    upload(client, source, "policy.md", "# Policy\n\nThe rollout begins on Monday.")
    drain(worker)
    key = str(uuid4())
    reasoning.failure = ContextMeshError("provider_unavailable")
    failed = frames(send(client, conversation_id, key))
    assert failed[-1][0] == "error"
    assert failed[-1][1]["code"] == "provider_unavailable"
    assert all(event != "delta" for event, _ in failed)
    reasoning.failure = None
    assert frames(send(client, conversation_id, key))[-1][0] == "completed"
    history = client.get(f"/api/v1/assistant/conversations/{conversation_id}/messages").json()
    assert [message["role"] for message in history["items"]] == ["user", "assistant"]


def test_stream_preflight_rejects_unknown_source_and_payload_reuse(client, conversation_id):
    response = send(client, conversation_id, "missing", source_ids=[str(uuid4())])
    assert response.status_code == 404
    frames(send(client, conversation_id, "same"))
    conflict = send(client, conversation_id, "same", message="Another question")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
