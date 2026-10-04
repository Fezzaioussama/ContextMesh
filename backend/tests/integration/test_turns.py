"""Canonical turn state races, short transactions, retries and release authorization."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from app.core.exceptions import ContextMeshError
from app.db.models.conversation import turns
from app.db.models.user import memberships
from app.domain.models import ModelReply, Usage
from app.services.assistant import Assistant
from sqlalchemy import delete, update

pytestmark = pytest.mark.integration
REPLY = ModelReply("Persisted assistant", Usage(10, 4))


def claim_outcome(turn_repository, identity, conversation_id, key):
    try:
        return turn_repository.claim(identity, conversation_id, key, "Question")
    except ContextMeshError as error:
        return error.code


def expire(engine, turn_id):
    with engine.begin() as connection:
        connection.execute(
            update(turns)
            .where(turns.c.id == turn_id)
            .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
        )


def test_concurrent_claims_create_only_one_visible_user_message(
    repository, identity, turn_repository
):
    conversation = repository.create(identity, "Concurrency")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(claim_outcome, turn_repository, identity, conversation.id, "first")
        second = pool.submit(claim_outcome, turn_repository, identity, conversation.id, "second")
        outcomes = [first.result(), second.result()]
    assert outcomes.count("turn_in_progress") == 1
    assert len(repository.messages(identity, conversation.id, 100, None).items) == 1


def test_same_key_active_claim_cannot_duplicate_user_message(repository, identity, turn_repository):
    conversation = repository.create(identity, "Active replay")
    turn_repository.claim(identity, conversation.id, "key", "Question")
    with pytest.raises(ContextMeshError) as failure:
        turn_repository.claim(identity, conversation.id, "key", "Question")
    assert failure.value.code == "turn_in_progress"
    assert len(repository.messages(identity, conversation.id, 100, None).items) == 1


def test_expired_execution_reuses_user_message_and_replaces_token(
    repository, identity, engine, turn_repository
):
    conversation = repository.create(identity, "Recover")
    original = turn_repository.claim(identity, conversation.id, "key", "Question")
    expire(engine, original.turn_id)
    replacement = turn_repository.claim(identity, conversation.id, "key", "Question")
    assert replacement.user_message == original.user_message
    assert replacement.token != original.token
    assert len(repository.messages(identity, conversation.id, 100, None).items) == 1


def test_stale_execution_cannot_complete_or_fail_replacement(
    repository, identity, engine, turn_repository
):
    conversation = repository.create(identity, "Fence")
    original = turn_repository.claim(identity, conversation.id, "key", "Question")
    expire(engine, original.turn_id)
    replacement = turn_repository.claim(identity, conversation.id, "key", "Question")
    with pytest.raises(ContextMeshError, match="already running"):
        turn_repository.complete(identity, original, REPLY)
    turn_repository.fail(identity, original)
    result = turn_repository.complete(identity, replacement, REPLY)
    assert result.user_message == original.user_message
    assert len(repository.messages(identity, conversation.id, 100, None).items) == 2


def test_expired_execution_cannot_release_before_reclaim(
    repository, identity, engine, turn_repository
):
    conversation = repository.create(identity, "Lease deadline")
    execution = turn_repository.claim(identity, conversation.id, "key", "Question")
    expire(engine, execution.turn_id)
    with pytest.raises(ContextMeshError):
        turn_repository.complete(identity, execution, REPLY)
    assert len(repository.messages(identity, conversation.id, 100, None).items) == 1


def test_new_key_reclaims_expired_conversation_and_fences_old_turn(
    repository, identity, engine, turn_repository
):
    conversation = repository.create(identity, "New attempt")
    original = turn_repository.claim(identity, conversation.id, "old", "Question")
    expire(engine, original.turn_id)
    replacement = turn_repository.claim(identity, conversation.id, "new", "Another question")
    with pytest.raises(ContextMeshError):
        turn_repository.complete(identity, original, REPLY)
    assert replacement.turn_id != original.turn_id


class TransactionCheckingModel:
    configured = True

    def __init__(self, engine):
        self.engine = engine
        self.checked_out = None

    def respond(self, history, message):
        self.checked_out = self.engine.pool.checkedout()
        return REPLY


def test_no_database_transaction_is_held_during_provider_io(
    repository, identity, engine, turn_repository
):
    model = TransactionCheckingModel(engine)
    assistant = Assistant(turn_repository, model)
    conversation = repository.create(identity, "Short transaction")
    result = assistant.send(identity, conversation.id, "key", "Question")
    assert model.checked_out == 0
    assert result.assistant_message.content == REPLY.content


class BlockingModel:
    configured = True

    def __init__(self):
        self.entered = Event()
        self.release = Event()

    def respond(self, history, message):
        self.entered.set()
        self.release.wait(timeout=5)
        return REPLY


def test_membership_revocation_during_provider_io_prevents_release(
    repository, identity, engine, turn_repository
):
    model = BlockingModel()
    assistant = Assistant(turn_repository, model)
    conversation = repository.create(identity, "Revoke while responding")
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(assistant.send, identity, conversation.id, "key", "Question")
        model.entered.wait(timeout=5)
        with engine.begin() as connection:
            connection.execute(
                delete(memberships).where(
                    memberships.c.workspace_id == identity.workspace_id,
                    memberships.c.subject == identity.subject,
                )
            )
        model.release.set()
        with pytest.raises(ContextMeshError) as failure:
            result.result(timeout=5)
    assert failure.value.code == "not_found"


def test_provider_context_is_bounded_to_twenty_prior_messages(
    repository, identity, model, turn_repository
):
    conversation = repository.create(identity, "Bounded history")
    assistant = Assistant(turn_repository, model)
    for index in range(12):
        assistant.send(identity, conversation.id, f"key-{index}", "Question")
    assert len(model.calls[-1][0]) == 20


def test_provider_context_has_forty_thousand_character_ceiling(
    repository, identity, model, turn_repository
):
    conversation = repository.create(identity, "Character bound")
    assistant = Assistant(turn_repository, model)
    for index in range(8):
        assistant.send(identity, conversation.id, f"key-{index}", "q" * 8000)
    history = model.calls[-1][0]
    assert sum(len(message.content) for message in history) <= 40000
