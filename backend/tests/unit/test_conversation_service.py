"""Direct callers receive the same input guarantees as the HTTP transport."""

from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

import pytest
from app.services.agent.policy import AgentPolicy
from app.services.assistant import Assistant
from app.services.chat_service import ConversationService
from app.services.ports.conversations import ConversationStore, TurnStore
from app.services.ports.retrieval import AnswerWorkflow, SourceCatalog
from app.services.rules.models import Conversation, Page
from app.utils.exceptions import ContextMeshError
from support import ScriptedReasoning


@pytest.fixture
def model():
    return ScriptedReasoning()


@pytest.fixture
def service_and_store(model):
    store = Mock(spec=ConversationStore)
    assistant = Assistant(
        Mock(spec=TurnStore), Mock(spec=SourceCatalog), Mock(spec=AnswerWorkflow), model
    )
    service = ConversationService(
        store,
        assistant,
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
