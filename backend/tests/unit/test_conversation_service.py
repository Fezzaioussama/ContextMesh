"""Direct callers receive the same input guarantees as the HTTP transport."""

import time
from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

import pytest
from app.data.chat import ChatRepository
from app.data.retrieval import RetrievalRepository
from app.services import chat
from app.services.agent.graph import AgentWorkflow
from app.services.agent.state import AgentPolicy
from app.services.chat import ConversationService, short_title
from app.services.models import Conversation, Execution, Message, Page
from app.utils.errors import ContextMeshError
from support import ScriptedReasoning, fixture_outcome


@pytest.fixture
def model():
    return ScriptedReasoning()


@pytest.fixture
def service_and_store(model):
    store = Mock(spec=ChatRepository)
    service = ConversationService(
        store,
        Mock(spec=RetrievalRepository),
        Mock(spec=AgentWorkflow),
        model,
        provider="openrouter",
        model_name="fixture",
        embedding_model="vendor/embed",
        max_output_tokens=1024,
        policy=AgentPolicy(),
    )
    return service, store


def test_create_normalizes_title_before_persistence(service_and_store, identity):
    service, store = service_and_store
    now = datetime.now(UTC)
    store.create.return_value = Conversation(uuid4(), "A title", now, now)
    result = service.create(identity, "  A title  ")
    assert result.title == "A title"
    store.create.assert_called_once_with(identity, "A title")


@pytest.mark.parametrize("title", ["", "   ", "x" * 101])
def test_invalid_title_never_reaches_persistence(service_and_store, identity, title):
    service, store = service_and_store
    with pytest.raises(ContextMeshError, match="title|Title"):
        service.create(identity, title)
    store.create.assert_not_called()


@pytest.mark.parametrize("limit", [0, 51])
def test_conversation_limits_apply_without_http(service_and_store, identity, limit):
    service, store = service_and_store
    with pytest.raises(ContextMeshError, match="page limit"):
        service.conversations(identity, limit, None)
    store.conversations.assert_not_called()


@pytest.mark.parametrize("limit", [0, 101])
def test_message_limits_apply_without_http(service_and_store, identity, limit):
    service, store = service_and_store
    with pytest.raises(ContextMeshError, match="page limit"):
        service.messages(identity, uuid4(), limit, None)
    store.messages.assert_not_called()


def test_oversized_cursor_is_rejected_before_persistence(service_and_store, identity):
    service, store = service_and_store
    with pytest.raises(ContextMeshError, match="pagination cursor"):
        service.conversations(identity, 20, "x" * 2049)
    store.conversations.assert_not_called()


def test_valid_page_returns_typed_values_and_continuation(service_and_store, identity):
    service, store = service_and_store
    now = datetime.now(UTC)
    conversation = Conversation(uuid4(), "Title", now, now)
    store.conversations.return_value = Page((conversation,), "next")
    result = service.conversations(identity, 1, "current")
    assert (result.items, result.next_cursor) == ((conversation,), "next")
    store.conversations.assert_called_once_with(identity, 1, "current")


def test_metadata_reflects_current_provider_availability(service_and_store, model):
    service, _ = service_and_store
    initial = service.metadata()
    model.configured = False
    current = service.metadata()
    assert (initial.provider, initial.model, initial.embedding_model) == (
        "openrouter",
        "fixture",
        "vendor/embed",
    )
    assert (initial.configured, current.configured) == (True, False)


# Naming a conversation from its first question


@pytest.fixture
def workflow():
    workflow = Mock(spec=AgentWorkflow)
    workflow.run.return_value = fixture_outcome()
    return workflow


@pytest.fixture
def turns(model, workflow):
    store = Mock(spec=ChatRepository)
    store.replay.return_value = None
    service = ConversationService(
        store,
        Mock(spec=RetrievalRepository),
        workflow,
        model,
        provider="openrouter",
        model_name="fixture",
        embedding_model="vendor/embed",
        max_output_tokens=1024,
        policy=AgentPolicy(),
    )
    return service, store


def claimed(store, question, history=()):
    now = datetime.now(UTC)
    turn_id, conversation_id = uuid4(), uuid4()
    message = Message(uuid4(), turn_id, "user", question, now)
    store.claim.return_value = Execution(turn_id, conversation_id, uuid4(), message, history)
    return conversation_id


def test_first_question_names_the_conversation(turns, model, identity):
    service, store = turns
    model.script["conversation_title"] = lambda content: {"title": " “Rollout plan.” "}
    conversation_id = claimed(store, "When does the rollout start?")
    service.send(identity, conversation_id, "key", "When does the rollout start?")
    store.rename_untitled.assert_called_once_with(identity, conversation_id, "Rollout plan")
    assert model.contents("conversation_title") == [{"message": "When does the rollout start?"}]


def test_follow_up_questions_never_rename(turns, model, identity):
    service, store = turns
    now = datetime.now(UTC)
    earlier = (Message(uuid4(), uuid4(), "user", "Earlier question", now),)
    conversation_id = claimed(store, "And then?", earlier)
    service.send(identity, conversation_id, "key", "And then?")
    store.rename_untitled.assert_not_called()
    assert "conversation_title" not in model.names()


def test_title_falls_back_to_the_question_when_the_model_fails(turns, model, identity):
    service, store = turns
    model.failure = ContextMeshError("provider_unavailable")
    conversation_id = claimed(store, "When does the rollout start?")
    service.send(identity, conversation_id, "key", "When does the rollout start?")
    store.rename_untitled.assert_called_once_with(
        identity, conversation_id, "When does the rollout start"
    )


def test_a_slow_title_never_holds_up_the_answer(turns, model, identity, monkeypatch):
    service, store = turns
    monkeypatch.setattr(chat, "TITLE_GRACE_SECONDS", 0.01)

    def slow(content):
        time.sleep(0.3)
        return {"title": "Too late"}

    model.script["conversation_title"] = slow
    conversation_id = claimed(store, "When does the rollout start?")
    started = time.monotonic()
    service.send(identity, conversation_id, "key", "When does the rollout start?")
    assert time.monotonic() - started < 0.25
    store.rename_untitled.assert_called_once_with(
        identity, conversation_id, "When does the rollout start"
    )


def test_failed_turn_keeps_the_default_title(turns, workflow, identity):
    service, store = turns
    workflow.run.side_effect = ContextMeshError("agent_deadline_exceeded")
    conversation_id = claimed(store, "When does the rollout start?")
    with pytest.raises(ContextMeshError):
        service.send(identity, conversation_id, "key", "When does the rollout start?")
    store.rename_untitled.assert_not_called()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("  Rollout\n plan  ", "Rollout plan"),
        ('"Quoted title."', "Quoted title"),
        ("word " * 20, "word word word word word word word word word word word word…"),
        ("x" * 80, "x" * 60 + "…"),
        ("?!", ""),
        (None, ""),
    ],
)
def test_short_title_is_one_trimmed_line_cut_at_a_word(text, expected):
    assert short_title(text) == expected
