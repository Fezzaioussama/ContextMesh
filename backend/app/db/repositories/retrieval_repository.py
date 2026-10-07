"""Query-side canonical reads: catalog, lexical candidates, and authorized hydration."""

import re
from uuid import UUID

from sqlalchemy import Engine, RowMapping, and_, func, select
from sqlalchemy.sql import Select

from app.core.exceptions import unavailable_resource
from app.core.security import Identity
from app.db.models.knowledge import chunks, documents, index_generations, sources
from app.db.repositories.access import require_membership
from app.db.repositories.knowledge_access import published_chunks, published_scope
from app.domain.answers import Locator
from app.domain.knowledge import CatalogSource, Passage

LEXEME = re.compile(r"\w+")
MAX_QUERY_TERMS = 32


def lexical_expression(query: str) -> str:
    """OR-combined plain word lexemes; operators in user text never reach to_tsquery."""
    return " | ".join(LEXEME.findall(query)[:MAX_QUERY_TERMS])


class RetrievalRepository:
    def __init__(self, engine: Engine):
        self._engine = engine

    def eligible(
        self, identity: Identity, source_ids: tuple[UUID, ...] | None
    ) -> tuple[CatalogSource, ...]:
        query = _catalog(identity.workspace_id, source_ids)
        with self._engine.begin() as connection:
            require_membership(connection, identity)
            rows = connection.execute(query).mappings().all()
        if source_ids is not None and len(rows) != len(source_ids):
            raise unavailable_resource()
        return tuple(_catalog_value(row) for row in rows)

    def search(
        self, identity: Identity, source_ids: tuple[UUID, ...], query: str, limit: int
    ) -> tuple[UUID, ...]:
        expression = lexical_expression(query)
        if not expression:
            return ()
        terms = func.to_tsquery("english", expression)
        statement = (
            select(chunks.c.id)
            .select_from(published_chunks())
            .where(*published_scope(identity.workspace_id, source_ids))
            .where(chunks.c.search_vector.op("@@")(terms))
            .order_by(func.ts_rank_cd(chunks.c.search_vector, terms).desc(), chunks.c.id)
            .limit(limit)
        )
        with self._engine.begin() as connection:
            require_membership(connection, identity)
            return tuple(connection.execute(statement).scalars())

    def hydrate(
        self, identity: Identity, source_ids: tuple[UUID, ...], chunk_ids: tuple[UUID, ...]
    ) -> tuple[Passage, ...]:
        if not chunk_ids:
            return ()
        statement = (
            _passage_columns()
            .select_from(published_chunks())
            .where(*published_scope(identity.workspace_id, source_ids))
            .where(chunks.c.id.in_(chunk_ids))
        )
        with self._engine.begin() as connection:
            require_membership(connection, identity)
            rows = connection.execute(statement).mappings().all()
        return _in_requested_order(rows, chunk_ids)

    def passage(
        self, identity: Identity, document_id: UUID, version_id: UUID, chunk_id: UUID
    ) -> Passage:
        """Historical versions stay readable while the document and source are live."""
        statement = (
            _passage_columns()
            .select_from(_historical_chunks())
            .where(
                chunks.c.id == chunk_id,
                chunks.c.document_id == document_id,
                chunks.c.workspace_id == identity.workspace_id,
                index_generations.c.document_version_id == version_id,
                index_generations.c.state.in_(("published", "superseded")),
                documents.c.deleted_at.is_(None),
                sources.c.deleted_at.is_(None),
            )
        )
        with self._engine.begin() as connection:
            require_membership(connection, identity)
            row = connection.execute(statement).mappings().first()
        if row is None:
            raise unavailable_resource()
        return passage_value(row)


def _in_requested_order(rows: list[RowMapping], chunk_ids: tuple[UUID, ...]) -> tuple[Passage, ...]:
    by_id = {row["chunk_id"]: passage_value(row) for row in rows}
    return tuple(by_id[item] for item in chunk_ids if item in by_id)


def _catalog(workspace_id: UUID, source_ids: tuple[UUID, ...] | None) -> Select:
    live = and_(documents.c.source_id == sources.c.id, documents.c.deleted_at.is_(None))
    query = (
        select(
            sources.c.id,
            sources.c.name,
            sources.c.description,
            func.count(documents.c.active_generation_id).label("searchable_count"),
        )
        .select_from(sources.outerjoin(documents, live))
        .where(sources.c.workspace_id == workspace_id, sources.c.deleted_at.is_(None))
        .group_by(sources.c.id)
        .order_by(sources.c.created_at, sources.c.id)
    )
    if source_ids is not None:
        query = query.where(sources.c.id.in_(source_ids))
    return query


def _catalog_value(row: RowMapping) -> CatalogSource:
    return CatalogSource(row["id"], row["name"], row["description"], row["searchable_count"])


def _historical_chunks():
    return (
        chunks.join(index_generations, index_generations.c.id == chunks.c.generation_id)
        .join(documents, documents.c.id == chunks.c.document_id)
        .join(sources, sources.c.id == chunks.c.source_id)
    )


def _passage_columns() -> Select:
    return select(
        chunks.c.id.label("chunk_id"),
        chunks.c.source_id,
        chunks.c.document_id,
        index_generations.c.document_version_id,
        chunks.c.generation_id,
        sources.c.name.label("source_name"),
        documents.c.title,
        documents.c.source_uri,
        chunks.c.heading_path,
        chunks.c.line_start,
        chunks.c.line_end,
        chunks.c.page,
        chunks.c.slide,
        chunks.c.content,
    )


def passage_value(row: RowMapping) -> Passage:
    return Passage(
        row["chunk_id"],
        row["source_id"],
        row["document_id"],
        row["document_version_id"],
        row["generation_id"],
        row["source_name"],
        row["title"],
        Locator(
            tuple(row["heading_path"]),
            row["line_start"],
            row["line_end"],
            row["page"],
            row["slide"],
        ),
        row["content"],
        row["source_uri"],
    )
