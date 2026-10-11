"""Conversations, idempotent turns, and saved answers, in short scoped transactions.

A turn is claimed with an execution token and a lease; only the holder of a live token
can complete or fail it. No transaction stays open while the agent runs.
"""

import base64
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import (
    Connection,
    Engine,
    RowMapping,
    Uuid,
    and_,
    cast,
    func,
    insert,
    or_,
    select,
    true,
    tuple_,
    update,
)

from app.data.db import (
    answers,
    chunks,
    citations,
    conversations,
    documents,
    member_role,
    messages,
    sources,
    turns,
)
from app.services.models import (
    DEFAULT_TITLE,
    WITHHELD_TEXT,
    AgentOutcome,
    Answer,
    Citation,
    Claim,
    Conversation,
    Execution,
    Identity,
    Locator,
    Message,
    Page,
    TraceStage,
    TurnInput,
    TurnResult,
    Usage,
    withheld_answer,
)
from app.utils.errors import ContextMeshError

HISTORY_MESSAGES = 20
HISTORY_CHARACTERS = 40_000


class ChatRepository:
    def __init__(self, engine: Engine, lease_seconds: int = 90):
        self.engine = engine
        self.lease_seconds = lease_seconds

    def create(self, identity: Identity, title: str) -> Conversation:
        now = datetime.now(UTC)
        value = Conversation(uuid4(), title, now, now)
        with self.engine.begin() as connection:
            member_role(connection, identity)
            connection.execute(
                insert(conversations).values(
                    id=value.id,
                    workspace_id=identity.workspace_id,
                    subject=identity.subject,
                    title=title,
                    created_at=now,
                    updated_at=now,
                )
            )
        return value

    def rename_untitled(self, identity: Identity, conversation_id: UUID, title: str) -> None:
        """Name a conversation that still has the default title; a chosen title stays."""
        with self.engine.begin() as connection:
            member_role(connection, identity)
            connection.execute(
                update(conversations)
                .where(
                    _owned(identity),
                    conversations.c.id == conversation_id,
                    conversations.c.title == DEFAULT_TITLE,
                )
                .values(title=title)
            )

    def conversations(
        self, identity: Identity, limit: int, cursor: str | None
    ) -> Page[Conversation]:
        """Newest first; the cursor only continues this requester's own listing."""
        scope = f"conversations:{identity.workspace_id}:{identity.subject}"
        position = decode_cursor(cursor, scope)
        query = select(conversations).where(_owned(identity))
        if position:
            query = query.where(tuple_(conversations.c.created_at, conversations.c.id) < position)
        query = query.order_by(conversations.c.created_at.desc(), conversations.c.id.desc())
        with self.engine.begin() as connection:
            member_role(connection, identity)
            rows = connection.execute(query.limit(limit + 1)).mappings().all()
        return _page([_conversation(row) for row in rows], limit, scope)

    def messages(
        self, identity: Identity, conversation_id: UUID, limit: int, cursor: str | None
    ) -> Page[Message]:
        """Oldest first, with saved answers attached."""
        scope = f"messages:{identity.workspace_id}:{identity.subject}:{conversation_id}"
        position = decode_cursor(cursor, scope)
        query = select(messages).where(messages.c.conversation_id == conversation_id)
        if position:
            query = query.where(tuple_(messages.c.created_at, messages.c.id) > position)
        query = query.order_by(messages.c.created_at, messages.c.id)
        with self.engine.begin() as connection:
            _require_conversation(connection, identity, conversation_id)
            rows = connection.execute(query.limit(limit + 1)).mappings().all()
            items = _with_answers(connection, [_message(row) for row in rows])
        return _page(items, limit, scope)

    def replay(
        self, identity: Identity, conversation_id: UUID, key: str, turn: TurnInput
    ) -> TurnResult | None:
        """A completed turn is returned even if its source scope has since changed."""
        with self.engine.begin() as connection:
            _require_conversation(connection, identity, conversation_id)
            existing = _existing_turn(connection, conversation_id, key, turn)
            if existing is None or existing["status"] != "completed":
                return None
            return _completed(connection, existing)

    def claim(
        self, identity: Identity, conversation_id: UUID, key: str, turn: TurnInput
    ) -> Execution | TurnResult:
        """Start (or restart) the turn for `key`; one turn runs per conversation."""
        with self.engine.begin() as connection:
            _require_conversation(connection, identity, conversation_id, lock=True)
            existing = _existing_turn(connection, conversation_id, key, turn)
            if existing is not None and existing["status"] == "completed":
                return _completed(connection, existing)
            now = datetime.now(UTC)
            _expire_stale_turns(connection, conversation_id, now)
            token = uuid4()
            values = {
                "execution_token": token,
                "lease_until": now + timedelta(seconds=self.lease_seconds),
                "status": "running",
                "safe_error_code": None,
            }
            if existing is None:
                turn_id = _new_turn(connection, conversation_id, key, turn, values, now)
            else:
                turn_id = existing["id"]
                connection.execute(update(turns).where(turns.c.id == turn_id).values(**values))
            return Execution(
                turn_id,
                conversation_id,
                token,
                _turn_message(connection, turn_id, "user"),
                _history(connection, conversation_id, turn_id),
            )

    def complete(
        self, identity: Identity, execution: Execution, outcome: AgentOutcome
    ) -> TurnResult:
        with self.engine.begin() as connection:
            _require_conversation(connection, identity, execution.conversation_id, lock=True)
            _require_live_execution(connection, execution)
            _require_visible_evidence(connection, outcome.answer)
            reply = _save_reply(connection, execution, outcome)
        return TurnResult(
            execution.turn_id,
            execution.conversation_id,
            execution.user_message,
            reply,
            outcome.usage,
            outcome.trace,
        )

    def fail(self, identity: Identity, execution: Execution, code: str) -> None:
        with self.engine.begin() as connection:
            _require_conversation(connection, identity, execution.conversation_id, lock=True)
            connection.execute(
                update(turns)
                .where(
                    turns.c.id == execution.turn_id,
                    turns.c.execution_token == execution.token,
                    turns.c.status == "running",
                )
                .values(status="failed", safe_error_code=code[:50])
            )


# Access


def _owned(identity: Identity):
    return and_(
        conversations.c.workspace_id == identity.workspace_id,
        conversations.c.subject == identity.subject,
    )


def _require_conversation(
    connection: Connection, identity: Identity, conversation_id: UUID, lock: bool = False
) -> None:
    """Another requester's conversation is indistinguishable from a missing one."""
    member_role(connection, identity)
    query = select(conversations.c.id).where(
        _owned(identity), conversations.c.id == conversation_id
    )
    if lock:
        query = query.with_for_update()
    if connection.execute(query).first() is None:
        raise ContextMeshError("not_found")


# Cursor pagination


def decode_cursor(cursor: str | None, scope: str) -> tuple[datetime, UUID] | None:
    if cursor is None:
        return None
    try:
        value = json.loads(base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True))
        fields = ("scope", "time", "id")
        if not isinstance(value, dict) or not all(isinstance(value.get(f), str) for f in fields):
            raise ValueError("malformed cursor")
        if value["scope"] != scope:
            raise ValueError("cursor from another collection")
        stamp = datetime.fromisoformat(value["time"])
        if stamp.tzinfo is None:
            raise ValueError("timezone required")
        return stamp, UUID(value["id"])
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ContextMeshError("invalid_input", "The pagination cursor is invalid.") from None


def _page[T: Conversation | Message](items: list[T], limit: int, scope: str) -> Page[T]:
    visible = items[:limit]
    cursor = None
    if len(items) > limit:
        last = visible[-1]
        value = {"scope": scope, "time": last.created_at.isoformat(), "id": str(last.id)}
        cursor = base64.urlsafe_b64encode(json.dumps(value).encode()).decode()
    return Page(tuple(visible), cursor)


# Turns


def _existing_turn(
    connection: Connection, conversation_id: UUID, key: str, turn: TurnInput
) -> RowMapping | None:
    query = select(turns).where(
        turns.c.conversation_id == conversation_id, turns.c.idempotency_key == key
    )
    row = connection.execute(query).mappings().first()
    if row is not None and row["payload_hash"] != turn.fingerprint:
        raise ContextMeshError("idempotency_conflict")
    return row


def _expire_stale_turns(connection: Connection, conversation_id: UUID, now: datetime) -> None:
    """Fail turns whose lease ran out; a turn still running blocks a new one."""
    connection.execute(
        update(turns)
        .where(
            turns.c.conversation_id == conversation_id,
            turns.c.status == "running",
            turns.c.lease_until <= now,
        )
        .values(status="failed", safe_error_code="execution_expired")
    )
    running = select(turns.c.id).where(
        turns.c.conversation_id == conversation_id, turns.c.status == "running"
    )
    if connection.execute(running).first() is not None:
        raise ContextMeshError("turn_in_progress")


def _new_turn(
    connection: Connection,
    conversation_id: UUID,
    key: str,
    turn: TurnInput,
    values: dict[str, object],
    now: datetime,
) -> UUID:
    turn_id = uuid4()
    connection.execute(
        insert(turns).values(
            id=turn_id,
            conversation_id=conversation_id,
            idempotency_key=key,
            payload_hash=turn.fingerprint,
            **values,
        )
    )
    connection.execute(
        insert(messages).values(
            id=uuid4(),
            conversation_id=conversation_id,
            turn_id=turn_id,
            role="user",
            content=turn.message,
            created_at=now,
        )
    )
    _touch(connection, conversation_id, now)
    return turn_id


def _touch(connection: Connection, conversation_id: UUID, now: datetime) -> None:
    connection.execute(
        update(conversations).where(conversations.c.id == conversation_id).values(updated_at=now)
    )


def _require_live_execution(connection: Connection, execution: Execution) -> None:
    row = connection.execute(select(turns).where(turns.c.id == execution.turn_id)).mappings().one()
    live = (
        row["execution_token"] == execution.token
        and row["status"] == "running"
        and row["lease_until"] > datetime.now(UTC)
    )
    if not live:
        raise ContextMeshError("turn_in_progress")


def _turn_message(connection: Connection, turn_id: UUID, role: str) -> Message:
    query = select(messages).where(messages.c.turn_id == turn_id, messages.c.role == role)
    row = connection.execute(query).mappings().one()
    return _with_answers(connection, [_message(row)])[0]


def _completed(connection: Connection, row: RowMapping) -> TurnResult:
    assistant = _turn_message(connection, row["id"], "assistant")
    return TurnResult(
        row["id"],
        row["conversation_id"],
        _turn_message(connection, row["id"], "user"),
        assistant,
        Usage(row["input_tokens"], row["output_tokens"]),
        assistant.trace,
    )


def _history(connection: Connection, conversation_id: UUID, turn_id: UUID) -> tuple[Message, ...]:
    """The latest earlier messages, oldest first, within a message and character budget."""
    query = (
        select(messages)
        .where(messages.c.conversation_id == conversation_id, messages.c.turn_id != turn_id)
        .order_by(messages.c.created_at.desc(), messages.c.id.desc())
        .limit(HISTORY_MESSAGES)
    )
    rows = connection.execute(query).mappings().all()
    selected: list[Message] = []
    length = 0
    for message in _with_answers(connection, [_message(row) for row in rows]):
        length += len(message.content)
        if length > HISTORY_CHARACTERS:
            break
        selected.append(message)
    return tuple(reversed(selected))


def _conversation(row: RowMapping) -> Conversation:
    return Conversation(row["id"], row["title"], row["created_at"], row["updated_at"])


def _message(row: RowMapping) -> Message:
    return Message(row["id"], row["turn_id"], row["role"], row["content"], row["created_at"])


# Saved answers


def _save_reply(connection: Connection, execution: Execution, outcome: AgentOutcome) -> Message:
    now = datetime.now(UTC)
    answer = outcome.answer
    message = Message(
        uuid4(), execution.turn_id, "assistant", answer.text, now, answer, outcome.trace
    )
    connection.execute(
        insert(messages).values(
            id=message.id,
            conversation_id=execution.conversation_id,
            turn_id=execution.turn_id,
            role="assistant",
            content=answer.text,
            created_at=now,
        )
    )
    connection.execute(
        insert(answers).values(
            message_id=message.id,
            status=answer.status,
            claims=[
                {"id": claim.id, "text": claim.text, "citation_ids": list(claim.citation_ids)}
                for claim in answer.claims
            ],
            gaps=list(answer.gaps),
            trace=[{"stage": item.stage, "summary": item.summary} for item in outcome.trace],
            consulted_document_ids=[str(item) for item in outcome.consulted],
        )
    )
    if answer.citations:
        rows = [_citation_row(message.id, citation) for citation in answer.citations]
        connection.execute(insert(citations), rows)
    connection.execute(
        update(turns)
        .where(turns.c.id == execution.turn_id)
        .values(
            status="completed",
            input_tokens=outcome.usage.input_tokens,
            output_tokens=outcome.usage.output_tokens,
        )
    )
    _touch(connection, execution.conversation_id, now)
    return message


def _require_visible_evidence(connection: Connection, answer: Answer) -> None:
    """Release-time recheck: every cited chunk must still belong to live content."""
    cited = {citation.chunk_id for citation in answer.citations}
    if not cited:
        return
    query = (
        select(func.count(chunks.c.id))
        .select_from(
            chunks.join(documents, documents.c.id == chunks.c.document_id).join(
                sources, sources.c.id == chunks.c.source_id
            )
        )
        .where(
            chunks.c.id.in_(cited),
            documents.c.deleted_at.is_(None),
            sources.c.deleted_at.is_(None),
        )
    )
    if connection.execute(query).scalar_one() != len(cited):
        raise ContextMeshError("evidence_changed")


def _with_answers(connection: Connection, items: list[Message]) -> list[Message]:
    """Attach saved answers. An answer built from any since-deleted document is withheld,
    exposing neither its claims nor the trace that described them."""
    replies = [message.id for message in items if message.role == "assistant"]
    if not replies:
        return items
    saved = _saved_answers(connection, replies)
    hidden = _withheld(connection, replies)
    attached = []
    for message in items:
        if message.id in hidden:
            message = replace(message, content=WITHHELD_TEXT, answer=withheld_answer())
        elif message.id in saved:
            answer, trace = saved[message.id]
            message = replace(message, answer=replace(answer, text=message.content), trace=trace)
        attached.append(message)
    return attached


def _withheld(connection: Connection, message_ids: list[UUID]) -> set[UUID]:
    """Answers whose cited or merely consulted documents (or sources) were deleted."""
    consulted = (
        func.jsonb_array_elements_text(answers.c.consulted_document_ids)
        .table_valued("value")
        .lateral("consulted")
    )
    query = (
        select(answers.c.message_id)
        .select_from(
            answers.join(consulted, true())
            .join(documents, documents.c.id == cast(consulted.c.value, Uuid))
            .join(sources, sources.c.id == documents.c.source_id)
        )
        .where(answers.c.message_id.in_(message_ids))
        .where(or_(documents.c.deleted_at.is_not(None), sources.c.deleted_at.is_not(None)))
    )
    return set(connection.execute(query).scalars())


def _saved_answers(
    connection: Connection, message_ids: list[UUID]
) -> dict[UUID, tuple[Answer, tuple[TraceStage, ...]]]:
    cited: dict[UUID, list[Citation]] = {}
    query = (
        select(citations)
        .where(citations.c.message_id.in_(message_ids))
        .order_by(citations.c.message_id, citations.c.number)
    )
    for row in connection.execute(query).mappings():
        cited.setdefault(row["message_id"], []).append(_citation(row))
    saved = {}
    rows = connection.execute(select(answers).where(answers.c.message_id.in_(message_ids)))
    for row in rows.mappings():
        claims = tuple(
            Claim(item["id"], item["text"], tuple(item["citation_ids"])) for item in row["claims"]
        )
        found = tuple(cited.get(row["message_id"], ()))
        answer = Answer(row["status"], "", claims, found, tuple(row["gaps"]))
        trace = tuple(TraceStage(item["stage"], item["summary"]) for item in row["trace"])
        saved[row["message_id"]] = (answer, trace)
    return saved


def _citation_row(message_id: UUID, citation: Citation) -> dict[str, object]:
    locator = citation.locator
    return {
        "id": uuid4(),
        "message_id": message_id,
        "number": citation.number,
        "chunk_id": citation.chunk_id,
        "source_id": citation.source_id,
        "document_id": citation.document_id,
        "document_version_id": citation.document_version_id,
        "index_generation_id": citation.index_generation_id,
        "title": citation.title,
        "locator": {
            "heading_path": list(locator.heading_path),
            "line_start": locator.line_start,
            "line_end": locator.line_end,
            "page": locator.page,
            "slide": locator.slide,
        },
        "snippet": citation.snippet,
        "source_url": citation.source_url,
    }


def _citation(row: RowMapping) -> Citation:
    stored = row["locator"]
    locator = Locator(
        tuple(stored["heading_path"]),
        stored["line_start"],
        stored["line_end"],
        stored.get("page"),
        stored.get("slide"),
    )
    return Citation(
        f"citation-{row['number']}",
        row["number"],
        row["source_id"],
        row["document_id"],
        row["document_version_id"],
        row["index_generation_id"],
        row["chunk_id"],
        row["title"],
        locator,
        row["snippet"],
        row["source_url"],
    )
