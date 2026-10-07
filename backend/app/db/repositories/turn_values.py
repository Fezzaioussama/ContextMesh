"""Turn hydration and bounded saved context, with evidence-aware answer withholding."""

from typing import Literal
from uuid import UUID

from sqlalchemy import Connection, RowMapping, select

from app.db.models.conversation import messages
from app.db.repositories.access import message_value
from app.db.repositories.answer_records import with_answers
from app.domain.models import Message, TurnResult, Usage


def turn_message(
    connection: Connection, turn_id: UUID, role: Literal["user", "assistant"]
) -> Message:
    row = (
        connection.execute(
            select(messages).where(messages.c.turn_id == turn_id, messages.c.role == role)
        )
        .mappings()
        .one()
    )
    return with_answers(connection, [message_value(row)])[0]


def completed_result(connection: Connection, row: RowMapping) -> TurnResult:
    assistant = turn_message(connection, row["id"], "assistant")
    return TurnResult(
        row["id"],
        row["conversation_id"],
        turn_message(connection, row["id"], "user"),
        assistant,
        Usage(row["input_tokens"], row["output_tokens"]),
        assistant.trace,
    )


def bounded_history(
    connection: Connection, conversation_id: UUID, turn_id: UUID
) -> tuple[Message, ...]:
    query = select(messages).where(
        messages.c.conversation_id == conversation_id, messages.c.turn_id != turn_id
    )
    query = query.order_by(messages.c.created_at.desc(), messages.c.id.desc()).limit(20)
    rows = connection.execute(query).mappings().all()
    values = with_answers(connection, [message_value(row) for row in rows])
    return _within_character_budget(values)


def _within_character_budget(values: list[Message]) -> tuple[Message, ...]:
    selected = []
    length = 0
    for message in values:
        length += len(message.content)
        if length > 40000:
            break
        selected.append(message)
    return tuple(reversed(selected))
