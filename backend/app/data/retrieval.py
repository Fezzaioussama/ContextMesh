"""Query-side reads: the source catalog, keyword candidates, and authorized hydration.

Only `hydrate` and `passage` turn chunk ids into text, and both check that the chunk
belongs to a live, published document the requester may read.
"""

import re
from uuid import UUID

from sqlalchemy import Engine, RowMapping, Select, and_, func, select

from app.data.db import chunks, documents, index_generations, member_role, sources
from app.services.models import CatalogSource, Identity, Locator, Passage
from app.utils.errors import ContextMeshError

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
        """The requester's live sources; naming any unknown source fails the whole call."""
        live = and_(documents.c.source_id == sources.c.id, documents.c.deleted_at.is_(None))
        query = (
            select(
                sources.c.id,
                sources.c.name,
                sources.c.description,
                func.count(documents.c.active_generation_id).label("searchable_count"),
            )
            .select_from(sources.outerjoin(documents, live))
            .where(sources.c.workspace_id == identity.workspace_id, sources.c.deleted_at.is_(None))
            .group_by(sources.c.id)
            .order_by(sources.c.created_at, sources.c.id)
        )
        if source_ids is not None:
            query = query.where(sources.c.id.in_(source_ids))
        with self._engine.begin() as connection:
            member_role(connection, identity)
            rows = connection.execute(query).mappings().all()
        if source_ids is not None and len(rows) != len(source_ids):
            raise ContextMeshError("not_found")
        return tuple(
            CatalogSource(row["id"], row["name"], row["description"], row["searchable_count"])
            for row in rows
        )

    def search(
        self, identity: Identity, source_ids: tuple[UUID, ...], query: str, limit: int
    ) -> tuple[UUID, ...]:
        """PostgreSQL full-text candidates, best first."""
        expression = lexical_expression(query)
        if not expression:
            return ()
        terms = func.to_tsquery("english", expression)
        statement = (
            select(chunks.c.id)
            .select_from(_published_chunks())
            .where(*_published_scope(identity.workspace_id, source_ids))
            .where(chunks.c.search_vector.op("@@")(terms))
            .order_by(func.ts_rank_cd(chunks.c.search_vector, terms).desc(), chunks.c.id)
            .limit(limit)
        )
        with self._engine.begin() as connection:
            member_role(connection, identity)
            return tuple(connection.execute(statement).scalars())

    def hydrate(
        self, identity: Identity, source_ids: tuple[UUID, ...], chunk_ids: tuple[UUID, ...]
    ) -> tuple[Passage, ...]:
        """Passages for the candidates still published and in scope, in candidate order."""
        if not chunk_ids:
            return ()
        statement = (
            _passage_columns()
            .select_from(_published_chunks())
            .where(*_published_scope(identity.workspace_id, source_ids))
            .where(chunks.c.id.in_(chunk_ids))
        )
        with self._engine.begin() as connection:
            member_role(connection, identity)
            rows = connection.execute(statement).mappings().all()
        by_id = {row["chunk_id"]: _passage(row) for row in rows}
        return tuple(by_id[item] for item in chunk_ids if item in by_id)

    def passage(
        self, identity: Identity, document_id: UUID, version_id: UUID, chunk_id: UUID
    ) -> Passage:
        """A cited passage. Historical versions stay readable while the document and its
        source are live, so older answers keep working after a reindex."""
        statement = (
            _passage_columns()
            .select_from(
                chunks.join(index_generations, index_generations.c.id == chunks.c.generation_id)
                .join(documents, documents.c.id == chunks.c.document_id)
                .join(sources, sources.c.id == chunks.c.source_id)
            )
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
            member_role(connection, identity)
            row = connection.execute(statement).mappings().first()
        if row is None:
            raise ContextMeshError("not_found")
        return _passage(row)


def _published_chunks():
    """Chunks of the active published generation of each document."""
    return (
        chunks.join(
            documents,
            and_(
                documents.c.id == chunks.c.document_id,
                documents.c.active_generation_id == chunks.c.generation_id,
            ),
        )
        .join(sources, sources.c.id == chunks.c.source_id)
        .join(index_generations, index_generations.c.id == chunks.c.generation_id)
    )


def _published_scope(workspace_id: UUID, source_ids: tuple[UUID, ...]) -> tuple:
    return (
        chunks.c.workspace_id == workspace_id,
        chunks.c.source_id.in_(source_ids),
        documents.c.deleted_at.is_(None),
        sources.c.deleted_at.is_(None),
        sources.c.workspace_id == workspace_id,
        index_generations.c.state == "published",
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


def _passage(row: RowMapping) -> Passage:
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
