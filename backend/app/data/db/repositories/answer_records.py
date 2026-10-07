"""Persist grounded answers and withhold saved answers whose evidence was removed."""

from dataclasses import replace
from uuid import UUID, uuid4

from sqlalchemy import Connection, RowMapping, Uuid, cast, func, insert, or_, select, true

from app.data.db.models.answers import answers, citations
from app.data.db.models.knowledge import chunks, documents, sources
from app.services.rules.answers import (
    WITHHELD_TEXT,
    Answer,
    Citation,
    Claim,
    Locator,
    TraceStage,
    withheld_answer,
)
from app.services.rules.errors import evidence_changed
from app.services.rules.models import AgentOutcome, Message


def save_answer(connection: Connection, message_id: UUID, outcome: AgentOutcome) -> None:
    connection.execute(insert(answers).values(**_answer_row(message_id, outcome)))
    if outcome.answer.citations:
        rows = [_citation_row(message_id, citation) for citation in outcome.answer.citations]
        connection.execute(insert(citations), rows)


def _answer_row(message_id: UUID, outcome: AgentOutcome) -> dict[str, object]:
    return {
        "message_id": message_id,
        "status": outcome.answer.status,
        "claims": [_claim_json(claim) for claim in outcome.answer.claims],
        "gaps": list(outcome.answer.gaps),
        "trace": [{"stage": item.stage, "summary": item.summary} for item in outcome.trace],
        "consulted_document_ids": [str(item) for item in outcome.consulted],
    }


def require_visible_evidence(connection: Connection, answer: Answer) -> None:
    """Release-time recheck: every cited chunk must still belong to live content."""
    cited = {citation.chunk_id for citation in answer.citations}
    if not cited:
        return
    query = (
        select(func.count(chunks.c.id))
        .select_from(_live_chunks())
        .where(chunks.c.id.in_(cited), documents.c.deleted_at.is_(None))
        .where(sources.c.deleted_at.is_(None))
    )
    if connection.execute(query).scalar_one() != len(cited):
        raise evidence_changed()


Saved = tuple[Answer, tuple[TraceStage, ...]]


def with_answers(connection: Connection, messages: list[Message]) -> list[Message]:
    """Withheld answers expose neither claims nor the trace that described them."""
    replies = [message.id for message in messages if message.role == "assistant"]
    saved = _saved_answers(connection, replies)
    hidden = _withheld(connection, replies)
    return [_attached(message, saved, hidden) for message in messages]


def _attached(message: Message, saved: dict[UUID, Saved], hidden: set[UUID]) -> Message:
    if message.id in hidden:
        return replace(message, content=WITHHELD_TEXT, answer=withheld_answer())
    if message.id not in saved:
        return message
    answer, trace = saved[message.id]
    return replace(message, answer=replace(answer, text=message.content), trace=trace)


def _live_chunks():
    return chunks.join(documents, documents.c.id == chunks.c.document_id).join(
        sources, sources.c.id == chunks.c.source_id
    )


def _withheld(connection: Connection, message_ids: list[UUID]) -> set[UUID]:
    """Withhold answers built from any now-deleted document, cited or merely consulted."""
    if not message_ids:
        return set()
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


def _saved_answers(connection: Connection, message_ids: list[UUID]) -> dict[UUID, Saved]:
    if not message_ids:
        return {}
    rows = connection.execute(select(answers).where(answers.c.message_id.in_(message_ids)))
    cited = _citations_by_message(connection, message_ids)
    return {row["message_id"]: _saved(row, cited) for row in rows.mappings()}


def _saved(row: RowMapping, cited: dict[UUID, tuple[Citation, ...]]) -> Saved:
    trace = tuple(TraceStage(item["stage"], item["summary"]) for item in row["trace"])
    return _answer(row, cited.get(row["message_id"], ())), trace


def _citations_by_message(
    connection: Connection, message_ids: list[UUID]
) -> dict[UUID, tuple[Citation, ...]]:
    query = (
        select(citations)
        .where(citations.c.message_id.in_(message_ids))
        .order_by(citations.c.message_id, citations.c.number)
    )
    grouped: dict[UUID, list[Citation]] = {}
    for row in connection.execute(query).mappings():
        grouped.setdefault(row["message_id"], []).append(_citation(row))
    return {key: tuple(values) for key, values in grouped.items()}


def _answer(row: RowMapping, cited: tuple[Citation, ...]) -> Answer:
    claims = tuple(
        Claim(item["id"], item["text"], tuple(item["citation_ids"])) for item in row["claims"]
    )
    return Answer(row["status"], "", claims, cited, tuple(row["gaps"]))


def _claim_json(claim: Claim) -> dict[str, object]:
    return {"id": claim.id, "text": claim.text, "citation_ids": list(claim.citation_ids)}


def _citation_row(message_id: UUID, citation: Citation) -> dict[str, object]:
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
            "heading_path": list(citation.locator.heading_path),
            "line_start": citation.locator.line_start,
            "line_end": citation.locator.line_end,
            "page": citation.locator.page,
            "slide": citation.locator.slide,
        },
        "snippet": citation.snippet,
        "source_url": citation.source_url,
    }


def _citation(row: RowMapping) -> Citation:
    return Citation(
        f"citation-{row['number']}",
        row["number"],
        row["source_id"],
        row["document_id"],
        row["document_version_id"],
        row["index_generation_id"],
        row["chunk_id"],
        row["title"],
        _locator(row["locator"]),
        row["snippet"],
        row["source_url"],
    )


def _locator(stored: dict) -> Locator:
    return Locator(
        tuple(stored["heading_path"]),
        stored["line_start"],
        stored["line_end"],
        stored.get("page"),
        stored.get("slide"),
    )
