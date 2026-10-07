"""Canonical turn state races, short transactions, retries and release authorization."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from app.data.db.models.conversation import turns
from app.data.db.models.user import memberships
from app.data.db.repositories.retrieval_repository import RetrievalRepository
from app.services.assistant import Assistant
from app.services.rules.models import TurnInput
from app.utils.exceptions import ContextMeshError
from sqlalchemy import delete, update
from support import FixtureWorkflow, ScriptedReasoning, fixture_outcome

pytestmark = pytest.mark.integration
OUTCOME = fixture_outcome("Persisted assistant")
QUESTION = TurnInput("Question")


def claim_outcome(turn_repository, identity, conversation_id, key):
    try:
        return turn_repository.claim(identity, conversation_id, key, QUESTION)
    except ContextMeshError as error:
        return error.code


def expire(engine, turn_id):
    with engine.begin() as connection:
        connection.execute(
            update(turns)
            .where(turns.c.id == turn_id)
            .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
        )


def assistant_for(engine, turn_repository, workflow):
    return Assistant(turn_repository, RetrievalRepository(engine), workflow, ScriptedReasoning())


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
    turn_repository.claim(identity, conversation.id, "key", QUESTION)
    with pytest.raises(ContextMeshError) as failure:
        turn_repository.claim(identity, conversation.id, "key", QUESTION)
    assert failure.value.code == "turn_in_progress"
    assert len(repository.messages(identity, conversation.id, 100, None).items) == 1


def test_source_scope_is_part_of_the_idempotent_payload(repository, identity, turn_repository):
    conversation = repository.create(identity, "Scoped replay")
    execution = turn_repository.claim(identity, conversation.id, "key", QUESTION)
    turn_repository.complete(identity, execution, OUTCOME)
    scoped = TurnInput("Question", (identity.workspace_id,))
    with pytest.raises(ContextMeshError) as failure:
        turn_repository.claim(identity, conversation.id, "key", scoped)
    assert failure.value.code == "idempotency_conflict"


def test_expired_execution_reuses_user_message_and_replaces_token(
    repository, identity, engine, turn_repository
):
    conversation = repository.create(identity, "Recover")
    original = turn_repository.claim(identity, conversation.id, "key", QUESTION)
    expire(engine, original.turn_id)
    replacement = turn_repository.claim(identity, conversation.id, "key", QUESTION)
    assert replacement.user_message == original.user_message
    assert replacement.token != original.token
    assert len(repository.messages(identity, conversation.id, 100, None).items) == 1


def test_stale_execution_cannot_complete_or_fail_replacement(
    repository, identity, engine, turn_repository
):
    conversation = repository.create(identity, "Fence")
    original = turn_repository.claim(identity, conversation.id, "key", QUESTION)
    expire(engine, original.turn_id)
    replacement = turn_repository.claim(identity, conversation.id, "key", QUESTION)
    with pytest.raises(ContextMeshError, match="already running"):
        turn_repository.complete(identity, original, OUTCOME)
    turn_repository.fail(identity, original, "provider_unavailable")
    result = turn_repository.complete(identity, replacement, OUTCOME)
    assert result.user_message == original.user_message
    assert len(repository.messages(identity, conversation.id, 100, None).items) == 2


def test_expired_execution_cannot_release_before_reclaim(
    repository, identity, engine, turn_repository
):
    conversation = repository.create(identity, "Lease deadline")
    execution = turn_repository.claim(identity, conversation.id, "key", QUESTION)
    expire(engine, execution.turn_id)
    with pytest.raises(ContextMeshError):
        turn_repository.complete(identity, execution, OUTCOME)
    assert len(repository.messages(identity, conversation.id, 100, None).items) == 1


def test_new_key_reclaims_expired_conversation_and_fences_old_turn(
    repository, identity, engine, turn_repository
):
    conversation = repository.create(identity, "New attempt")
    original = turn_repository.claim(identity, conversation.id, "old", QUESTION)
    expire(engine, original.turn_id)
    replacement = turn_repository.claim(
        identity, conversation.id, "new", TurnInput("Another question")
    )
    with pytest.raises(ContextMeshError):
        turn_repository.complete(identity, original, OUTCOME)
    assert replacement.turn_id != original.turn_id


class TransactionCheckingWorkflow(FixtureWorkflow):
    def __init__(self, engine):
        super().__init__(OUTCOME)
        self.engine = engine
        self.checked_out = None

    def run(self, identity, question, history, catalog):
        self.checked_out = self.engine.pool.checkedout()
        return super().run(identity, question, history, catalog)


def test_no_database_transaction_is_held_during_agent_execution(
    repository, identity, engine, turn_repository
):
    workflow = TransactionCheckingWorkflow(engine)
    assistant = assistant_for(engine, turn_repository, workflow)
    conversation = repository.create(identity, "Short transaction")
    result = assistant.send(identity, conversation.id, "key", "Question")
    assert workflow.checked_out == 0
    assert result.assistant_message.content == OUTCOME.answer.text


class BlockingWorkflow(FixtureWorkflow):
    def __init__(self):
        super().__init__(OUTCOME)
        self.entered = Event()
        self.release = Event()

    def run(self, identity, question, history, catalog):
        self.entered.set()
        self.release.wait(timeout=5)
        return super().run(identity, question, history, catalog)


def test_membership_revocation_during_agent_execution_prevents_release(
    repository, identity, engine, turn_repository
):
    workflow = BlockingWorkflow()
    assistant = assistant_for(engine, turn_repository, workflow)
    conversation = repository.create(identity, "Revoke while responding")
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(assistant.send, identity, conversation.id, "key", "Question")
        workflow.entered.wait(timeout=5)
        with engine.begin() as connection:
            connection.execute(
                delete(memberships).where(
                    memberships.c.workspace_id == identity.workspace_id,
                    memberships.c.subject == identity.subject,
                )
            )
        workflow.release.set()
        with pytest.raises(ContextMeshError) as failure:
            result.result(timeout=5)
    assert failure.value.code == "not_found"


def test_agent_context_is_bounded_to_twenty_prior_messages(
    repository, identity, engine, turn_repository
):
    conversation = repository.create(identity, "Bounded history")
    workflow = FixtureWorkflow()
    assistant = assistant_for(engine, turn_repository, workflow)
    for index in range(12):
        assistant.send(identity, conversation.id, f"key-{index}", "Question")
    assert len(workflow.calls[-1][1]) == 20


def test_agent_context_has_forty_thousand_character_ceiling(
    repository, identity, engine, turn_repository
):
    conversation = repository.create(identity, "Character bound")
    workflow = FixtureWorkflow()
    assistant = assistant_for(engine, turn_repository, workflow)
    for index in range(8):
        assistant.send(identity, conversation.id, f"key-{index}", "q" * 8000)
    history = workflow.calls[-1][1]
    assert sum(len(message.content) for message in history) <= 40000
