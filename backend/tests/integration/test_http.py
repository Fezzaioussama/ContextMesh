"""Public assistant contract, grounded persistence, and isolation over real PostgreSQL."""

from uuid import uuid4

import pytest
from app.bootstrap.api import create_app
from app.core.security import Identity
from app.db.models.user import memberships
from app.domain.errors import provider_unavailable
from fastapi.testclient import TestClient
from sqlalchemy import delete
from support import ask, create_source, drain, upload

pytestmark = pytest.mark.integration
ROOT = "/api/v1/assistant"
GUIDE = """# Authentication ADR

## Decision
We use OIDC with a central identity provider.

## Rollout
Billing moves first, in March.
"""


@pytest.fixture
def source_id(client, worker):
    created = create_source(client)
    assert upload(client, created, "auth.md", GUIDE).status_code == 202
    drain(worker)
    return created


def history(client, conversation_id):
    return client.get(f"{ROOT}/conversations/{conversation_id}/messages").json()["items"]


def test_metadata_describes_bounded_agentic_retrieval(client):
    metadata = client.get(ROOT).json()
    assert (metadata["configured"], metadata["mode"], metadata["retrieval_enabled"]) == (
        True,
        "agentic_rag",
        True,
    )
    assert metadata["limits"]["max_retrieval_rounds"] == 3
    assert client.get("/health/ready").json() == {"status": "ready"}


DECISION = "We use OIDC with a central identity provider."


def test_answer_cites_canonical_evidence_that_resolves(client, conversation_id, source_id):
    response = ask(client, conversation_id, "Which identity provider approach did we decide?")
    body = response.json()
    message = body["assistant_message"]
    citation = message["answer"]["citations"][0]
    observed = (
        response.status_code,
        message["answer"]["status"],
        message["content"].endswith("[1]"),
        (citation["title"], citation["source_id"], citation["snippet"]),
        citation["locator"]["heading_path"],
    )
    assert observed == (
        200,
        "answered",
        True,
        ("auth.md", source_id, DECISION),
        ["Authentication ADR", "Decision"],
    )
    assert client.get(citation["evidence_path"]).json()["text"] == DECISION
    assert (body["trace"][0]["stage"], history(client, conversation_id)[-1]["trace"]) == (
        "plan",
        body["trace"],
    )


def test_replay_returns_saved_answer_without_new_model_calls(
    client, conversation_id, source_id, reasoning
):
    first = ask(client, conversation_id, "Which approach?", "turn-key")
    calls = len(reasoning.tasks)
    replay = ask(client, conversation_id, "  Which approach?  ", "turn-key")
    assert replay.json() == first.json()
    assert len(reasoning.tasks) == calls
    assert history(client, conversation_id) == [
        first.json()["user_message"],
        first.json()["assistant_message"],
    ]


def test_follow_up_planning_sees_saved_conversation(client, conversation_id, source_id, reasoning):
    first = ask(client, conversation_id, "Which approach did we choose?").json()
    ask(client, conversation_id, "And when does it roll out?")
    conversation = reasoning.contents("search_plan")[-1]["conversation"]
    assert [item["content"] for item in conversation] == [
        "Which approach did we choose?",
        first["assistant_message"]["content"],
    ]


def test_key_reuse_with_different_message_or_scope_conflicts(client, conversation_id, source_id):
    ask(client, conversation_id, "Hello", "turn-key")
    for conflict in (
        ask(client, conversation_id, "Different", "turn-key"),
        ask(client, conversation_id, "Hello", "turn-key", [source_id]),
    ):
        error = conflict.json()["error"]
        assert (conflict.status_code, error["code"], error["request_id"]) == (
            409,
            "idempotency_conflict",
            conflict.headers["X-Request-ID"],
        )


@pytest.mark.parametrize(
    "body",
    [
        {"message": "   "},
        {"message": "x" * 8001},
        {"message": "Hello", "workspace_id": str(uuid4())},
        {"message": 123},
        {"message": "Hello", "source_ids": []},
        {"message": "Hello", "source_ids": ["not-a-uuid"]},
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


def test_unknown_or_foreign_source_filter_is_unavailable_and_saves_nothing(
    client, conversation_id, settings, services, source_id
):
    with TestClient(create_app(settings, services, Identity(f"o-{uuid4()}", uuid4()))) as other:
        foreign = create_source(other, "Foreign")
    for scope in ([str(uuid4())], [foreign], [source_id, foreign]):
        response = ask(client, conversation_id, "Hello", source_ids=scope)
        assert (response.status_code, response.json()["error"]["code"]) == (404, "not_found")
    assert history(client, conversation_id) == []


def test_explicit_filter_bounds_planning_and_citations(
    client, worker, conversation_id, source_id, reasoning
):
    other = create_source(client, "Rollout notes", "Rollout schedules")
    upload(client, other, "rollout.md", "# Rollout\n\nOIDC rollout begins with billing.")
    drain(worker)
    answer = ask(client, conversation_id, "OIDC rollout", source_ids=[other]).json()
    planned = reasoning.contents("search_plan")[-1]["sources"]
    assert [item["id"] for item in planned] == [other]
    cited = {item["source_id"] for item in answer["assistant_message"]["answer"]["citations"]}
    assert cited == {other}


def test_without_indexed_documents_the_answer_is_an_evidence_gap(
    client, conversation_id, reasoning
):
    create_source(client, "Empty")
    answer = ask(client, conversation_id, "Anything?").json()["assistant_message"]["answer"]
    assert (answer["status"], answer["citations"]) == ("insufficient_evidence", [])
    assert reasoning.tasks == []


def test_missing_provider_is_safe_and_does_not_fake_reply(client, conversation_id, reasoning):
    reasoning.configured = False
    response = ask(client, conversation_id, "Hello")
    assert (response.status_code, response.json()["error"]["code"]) == (
        503,
        "provider_not_configured",
    )
    assert reasoning.tasks == []


def test_completed_replay_survives_provider_key_removal(
    client, conversation_id, source_id, reasoning
):
    first = ask(client, conversation_id, "Which approach?", "turn-key")
    reasoning.configured = False
    assert ask(client, conversation_id, "Which approach?", "turn-key").json() == first.json()


def test_provider_failure_retry_reuses_saved_user_message(
    client, conversation_id, source_id, reasoning
):
    reasoning.failure = provider_unavailable()
    failed = ask(client, conversation_id, "Which approach?", "turn-key")
    reasoning.failure = None
    retry = ask(client, conversation_id, "Which approach?", "turn-key")
    assert (failed.status_code, retry.status_code) == (503, 200)
    assert len(history(client, conversation_id)) == 2


def test_history_survives_api_restart(
    settings, identity, services, client, conversation_id, source_id
):
    first = ask(client, conversation_id, "Which approach?").json()
    with TestClient(create_app(settings, services, identity)) as restarted:
        saved = history(restarted, conversation_id)
    assert saved == [first["user_message"], first["assistant_message"]]


def test_deleted_evidence_withholds_saved_answers(client, conversation_id, source_id, worker):
    first = ask(client, conversation_id, "Which approach?", "turn-key").json()
    document_id = first["assistant_message"]["answer"]["citations"][0]["document_id"]
    assert client.delete(f"/api/v1/documents/{document_id}").status_code == 202
    saved = history(client, conversation_id)[-1]
    replay = ask(client, conversation_id, "Which approach?", "turn-key").json()
    evidence = client.get(first["assistant_message"]["answer"]["citations"][0]["evidence_path"])
    withheld = {"status": "withheld", "claims": [], "citations": [], "gaps": []}
    assert (saved["answer"], saved["trace"], "OIDC" in saved["content"]) == (withheld, [], False)
    assert (replay["assistant_message"]["answer"], replay["trace"], evidence.status_code) == (
        withheld,
        [],
        404,
    )
    drain(worker)


@pytest.mark.parametrize("dimension", ["subject", "workspace", "both"])
def test_other_requester_and_missing_ids_are_indistinguishable(
    settings, services, client, conversation_id, identity, dimension
):
    ask(client, conversation_id, "Hello")
    others = {
        "subject": Identity(f"other-{uuid4()}", identity.workspace_id),
        "workspace": Identity(identity.subject, uuid4()),
        "both": Identity(f"other-{uuid4()}", uuid4()),
    }
    with TestClient(create_app(settings, services, others[dimension])) as unauthorized:
        inaccessible = ask(unauthorized, conversation_id, "Hello")
        missing = ask(unauthorized, uuid4(), "Hello")
        listed = unauthorized.get(f"{ROOT}/conversations").json()
        saved = unauthorized.get(f"{ROOT}/conversations/{conversation_id}/messages")
    assert (inaccessible.status_code, missing.status_code, saved.status_code) == (404, 404, 404)
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
    ask(client, conversation_id, "Hello", "turn-key")
    with engine.begin() as connection:
        connection.execute(
            delete(memberships).where(
                memberships.c.workspace_id == identity.workspace_id,
                memberships.c.subject == identity.subject,
            )
        )
    assert ask(client, conversation_id, "Hello", "turn-key").status_code == 404
    assert client.get(f"{ROOT}/conversations/{conversation_id}/messages").status_code == 404
