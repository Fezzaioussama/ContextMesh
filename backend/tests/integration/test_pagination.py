"""Scoped stable continuation, malformed cursors and explicit page bounds."""

import base64
import json
from uuid import uuid4

import pytest
from app.core.exceptions import ContextMeshError
from app.db.models.conversation import (
    conversations,
    messages,
)
from app.domain.models import ModelReply, Usage
from sqlalchemy import update

pytestmark = pytest.mark.integration


def test_conversation_pages_have_stable_uuid_tiebreaker(repository, identity, engine):
    first = repository.create(identity, "One")
    second = repository.create(identity, "Two")
    with engine.begin() as connection:
        connection.execute(
            update(conversations)
            .where(conversations.c.id == second.id)
            .values(created_at=first.created_at)
        )
    page = repository.conversations(identity, 1, None)
    next_page = repository.conversations(identity, 1, page.next_cursor)
    assert [page.items[0].id, next_page.items[0].id] == sorted([first.id, second.id], reverse=True)
    assert next_page.next_cursor is None


def test_message_pages_advance_in_ascending_stable_order(
    repository, identity, engine, turn_repository
):
    conversation = repository.create(identity, "Page history")
    execution = turn_repository.claim(identity, conversation.id, "key", "Question")
    result = turn_repository.complete(identity, execution, ModelReply("Answer", Usage(1, 1)))
    with engine.begin() as connection:
        connection.execute(
            update(messages)
            .where(messages.c.id == result.assistant_message.id)
            .values(created_at=execution.user_message.created_at)
        )
    first = repository.messages(identity, conversation.id, 1, None)
    second = repository.messages(identity, conversation.id, 1, first.next_cursor)
    assert [first.items[0].id, second.items[0].id] == sorted(
        [result.user_message.id, result.assistant_message.id]
    )
    assert second.next_cursor is None


@pytest.mark.parametrize("cursor", ["garbage", "e30=", "bnVsbA=="])
def test_invalid_cursor_produces_safe_validation_failure(repository, identity, cursor):
    with pytest.raises(ContextMeshError) as error:
        repository.conversations(identity, 20, cursor)
    assert error.value.code == "invalid_input"


def test_cursor_cannot_continue_another_requester_collection(repository, identity):
    repository.create(identity, "One")
    repository.create(identity, "Two")
    page = repository.conversations(identity, 1, None)
    other = type(identity)(identity.subject, uuid4())
    with pytest.raises(ContextMeshError) as error:
        repository.conversations(other, 20, page.next_cursor)
    assert error.value.code == "invalid_input"


@pytest.mark.parametrize(
    "path", ["/api/v1/assistant/conversations?limit=51", "/api/v1/assistant/conversations?limit=0"]
)
def test_http_limits_are_validated(client, path):
    assert client.get(path).status_code == 422


def encode_http_cursor(value):
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode()


def scoped_http_cursor(identity, conversation_id, collection):
    scope = f"conversations:{identity.workspace_id}:{identity.subject}"
    if collection == "messages":
        scope = f"messages:{identity.workspace_id}:{identity.subject}:{conversation_id}"
    return {"scope": scope, "time": "2026-10-03T12:00:00+00:00", "id": str(uuid4())}


def collection_path(conversation_id, collection):
    root = "/api/v1/assistant/conversations"
    if collection == "messages":
        return f"{root}/{conversation_id}/messages"
    return root


@pytest.mark.parametrize("collection", ["conversations", "messages"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("id", 42),
        ("id", {}),
        ("id", []),
        ("id", True),
        ("id", None),
        ("scope", 42),
        ("scope", {}),
        ("scope", []),
        ("scope", True),
        ("scope", None),
        ("time", 42),
        ("time", {}),
        ("time", []),
        ("time", True),
        ("time", None),
        ("time", "invalid-time"),
        ("time", "2026-10-03T12:00:00"),
    ],
)
def test_http_rejects_cursor_fields_with_invalid_types_or_time(
    client, identity, conversation_id, collection, field, value
):
    cursor = scoped_http_cursor(identity, conversation_id, collection)
    cursor[field] = value
    response = client.get(
        collection_path(conversation_id, collection), params={"cursor": encode_http_cursor(cursor)}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


@pytest.mark.parametrize("collection", ["conversations", "messages"])
@pytest.mark.parametrize("value", [42, [], True, None, "cursor-string"])
def test_http_rejects_nonobject_cursor_root(client, conversation_id, collection, value):
    response = client.get(
        collection_path(conversation_id, collection), params={"cursor": encode_http_cursor(value)}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"
