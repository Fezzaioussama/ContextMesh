"""Streaming preserves turn fencing, preflight errors, and canonical answer release."""

import json
from datetime import UTC, datetime
from threading import BoundedSemaphore, Event
from unittest.mock import Mock
from uuid import uuid4

import pytest
from app.data.chat import ChatRepository
from app.data.retrieval import RetrievalRepository
from app.services.agent.graph import AgentWorkflow
from app.services.agent.state import AgentPolicy
from app.services.chat import ConversationService
from app.services.models import Execution, Message, TurnResult
from app.utils.errors import ContextMeshError
from app.utils.streaming import StreamEvent, emit
from support import ScriptedReasoning, fixture_outcome
from test_http_dependencies import client_for


@pytest.fixture
def streaming_service():
    store = Mock(spec=ChatRepository)
    workflow = Mock(spec=AgentWorkflow)
    catalog = Mock(spec=RetrievalRepository)
    catalog.eligible.return_value = ()
    now = datetime.now(UTC)
    user = Message(uuid4(), uuid4(), "user", "Question", now)
    execution = Execution(user.turn_id, uuid4(), uuid4(), user, (user,))
    store.replay.return_value = None
    store.claim.return_value = execution
    workflow.run.return_value = fixture_outcome()

    def complete(identity, claimed, outcome):
        reply = Message(
            uuid4(), user.turn_id, "assistant", outcome.answer.text, now, outcome.answer
        )
        return TurnResult(
            user.turn_id, execution.conversation_id, user, reply, outcome.usage, outcome.trace
        )

    store.complete.side_effect = complete
    service = ConversationService(
        store,
        catalog,
        workflow,
        ScriptedReasoning(),
        provider="openrouter",
        model_name="fixture",
        embedding_model="fixture",
        max_output_tokens=100,
        policy=AgentPolicy(),
    )
    return service, store, catalog, workflow, execution


def open_stream(parts, identity, key="same-key"):
    service, _, _, _, execution = parts
    return service.open_stream(identity, execution.conversation_id, key, "Question")


def test_grounded_content_is_withheld_until_canonical_persistence(
    streaming_service, identity, monkeypatch
):
    monkeypatch.setattr("app.services.chat_stream.HEARTBEAT_SECONDS", 0.01)
    _, store, _, workflow, _ = streaming_service
    reached, release = Event(), Event()
    normal_complete = store.complete.side_effect

    def save(*args):
        reached.set()
        assert release.wait(2)
        return normal_complete(*args)

    store.complete.side_effect = save
    stream = open_stream(streaming_service, identity)
    events = stream.events()
    first = next(events)
    assert first.event == "progress"
    assert reached.wait(2)
    # No grounded prose can be queued before the final database visibility check.
    assert next(events).event == "progress"
    assert not stream.finished.done()
    release.set()
    result = list(events)
    texts = [
        item.data["text"]
        for item in result
        if isinstance(item, StreamEvent) and item.event == "delta"
    ]
    assert texts == [workflow.run.return_value.answer.text]
    assert isinstance(result[-1], TurnResult)
    store.complete.assert_called_once()


def test_changed_evidence_emits_error_without_any_answer_text(streaming_service, identity):
    _, store, _, _, _ = streaming_service
    store.complete.side_effect = ContextMeshError("evidence_changed")
    events = list(open_stream(streaming_service, identity).events())
    assert events[-1].event == "error"
    assert events[-1].data["code"] == "evidence_changed"
    assert all(item.event != "delta" for item in events)
    store.fail.assert_called_once()


def test_direct_text_arrives_before_run_finishes_and_terminal_replaces_it(
    streaming_service, identity
):
    _, _, _, workflow, _ = streaming_service
    release = Event()

    def direct(*args):
        emit(StreamEvent("delta", {"text": "**Hello**"}))
        assert release.wait(2)
        return fixture_outcome()

    workflow.run.side_effect = direct
    stream = open_stream(streaming_service, identity)
    events = stream.events()
    assert next(events).event == "progress"
    assert next(events).data == {"text": "**Hello**"}
    assert not stream.finished.done()
    release.set()
    assert isinstance(list(events)[-1], TurnResult)


def test_disconnect_finishes_claim_for_same_key_replay_without_another_model_call(
    streaming_service, identity
):
    service, store, _, workflow, execution = streaming_service
    entered, release = Event(), Event()

    def answer(*args):
        entered.set()
        assert release.wait(2)
        return fixture_outcome()

    workflow.run.side_effect = answer
    stream = open_stream(streaming_service, identity)
    assert entered.wait(2)
    stream.close()
    release.set()
    stream.finished.result(timeout=2)
    saved = store.complete.side_effect(identity, execution, fixture_outcome())
    store.replay.return_value = saved
    replay = list(
        service.open_stream(identity, execution.conversation_id, "same-key", "Question").events()
    )
    assert replay[-1] == saved
    assert list(stream.events()) == []
    workflow.run.assert_called_once()
    store.claim.assert_called_once()


def test_slow_reader_fails_safely_instead_of_growing_an_unbounded_queue(
    streaming_service, identity
):
    _, store, _, workflow, _ = streaming_service

    def overflow(*args):
        for _ in range(200):
            emit(StreamEvent("delta", {"text": "a"}))
        return fixture_outcome()

    workflow.run.side_effect = overflow
    stream = open_stream(streaming_service, identity)
    stream.finished.result(timeout=2)
    events = list(stream.events())
    assert len(events) <= 128
    assert events[-1].event == "error"
    store.fail.assert_called_once()
    store.complete.assert_not_called()


@pytest.mark.parametrize(("code", "status"), [("not_found", 404), ("idempotency_conflict", 409)])
def test_authorization_and_conflicts_keep_http_status_before_sse_headers(
    streaming_service, identity, code, status
):
    service, store, _, workflow, execution = streaming_service
    store.replay.side_effect = ContextMeshError(code)
    with client_for(service, identity, True) as client:
        response = client.post(
            f"/api/v1/assistant/conversations/{execution.conversation_id}/messages/stream",
            json={"message": "Question"},
            headers={"Idempotency-Key": "key"},
        )
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    workflow.run.assert_not_called()


def test_stream_completed_json_matches_the_existing_turn_contract(streaming_service, identity):
    from app.controllers.chat import TurnResponse

    service, store, _, _, execution = streaming_service
    saved = store.complete.side_effect(identity, execution, fixture_outcome())
    store.replay.return_value = saved
    with client_for(service, identity, True) as client:
        response = client.post(
            f"/api/v1/assistant/conversations/{execution.conversation_id}/messages/stream",
            json={"message": "Question"},
            headers={"Idempotency-Key": "key"},
        )
    frames = [frame.split("\n", 1) for frame in response.text.strip().split("\n\n")]
    assert response.headers["content-type"].startswith("text/event-stream")
    assert [frame[0] for frame in frames] == ["event: progress", "event: delta", "event: completed"]
    terminal = json.loads(frames[-1][1].removeprefix("data: "))
    assert terminal == TurnResponse.model_validate(saved).model_dump(mode="json")


def test_invalid_source_scope_is_rejected_before_claim(streaming_service, identity):
    service, store, catalog, workflow, execution = streaming_service
    catalog.eligible.side_effect = ContextMeshError("not_found")
    with pytest.raises(ContextMeshError, match="unavailable"):
        service.open_stream(identity, execution.conversation_id, "key", "Question", (uuid4(),))
    store.claim.assert_not_called()
    workflow.run.assert_not_called()


def test_admission_is_bounded_and_capacity_returns_after_detached_run(
    streaming_service, identity, monkeypatch
):
    slots = BoundedSemaphore(1)
    monkeypatch.setattr("app.services.chat.SLOTS", slots)
    monkeypatch.setattr("app.services.chat_stream.SLOTS", slots)
    _, store, _, workflow, _ = streaming_service
    release = Event()

    def answer(*args):
        assert release.wait(2)
        return fixture_outcome()

    workflow.run.side_effect = answer
    stream = open_stream(streaming_service, identity)
    try:
        with pytest.raises(ContextMeshError) as error:
            open_stream(streaming_service, identity, "other")
        assert error.value.code == "turn_in_progress"
        store.claim.assert_called_once()
    finally:
        stream.close()
        release.set()
        stream.finished.result(timeout=2)
    assert slots.acquire(blocking=False)
    slots.release()
