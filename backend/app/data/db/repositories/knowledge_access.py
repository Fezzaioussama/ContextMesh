"""Canonical source/document scope checks and shared visibility predicates."""

from uuid import UUID

from sqlalchemy import Connection, RowMapping, Table, and_, select
from sqlalchemy.sql import FromClause
from sqlalchemy.sql.elements import ColumnElement

from app.data.db.models.knowledge import chunks, documents, index_generations, sources
from app.data.db.models.user import memberships
from app.data.db.repositories.access import require_membership
from app.services.rules.errors import forbidden_operation
from app.utils.exceptions import unavailable_resource
from app.utils.security import Identity

EDITOR_ROLES = frozenset({"owner", "editor"})


def require_editor(connection: Connection, identity: Identity) -> None:
    query = select(memberships.c.role).where(
        memberships.c.workspace_id == identity.workspace_id,
        memberships.c.subject == identity.subject,
    )
    role = connection.execute(query).scalar()
    if role is None:
        raise unavailable_resource()
    if role not in EDITOR_ROLES:
        raise forbidden_operation()


def require_source(
    connection: Connection, identity: Identity, source_id: UUID, lock: bool = False
) -> RowMapping:
    require_membership(connection, identity)
    query = select(sources).where(
        sources.c.id == source_id,
        sources.c.workspace_id == identity.workspace_id,
        sources.c.deleted_at.is_(None),
    )
    return _required(connection, query, lock, sources)


def require_document(
    connection: Connection, identity: Identity, document_id: UUID, lock: bool = False
) -> RowMapping:
    require_membership(connection, identity)
    query = (
        select(documents)
        .join(sources, sources.c.id == documents.c.source_id)
        .where(
            documents.c.id == document_id,
            documents.c.workspace_id == identity.workspace_id,
            documents.c.deleted_at.is_(None),
            sources.c.deleted_at.is_(None),
        )
    )
    return _required(connection, query, lock, documents)


def _required(connection: Connection, query, lock: bool, table: Table) -> RowMapping:
    if lock:
        query = query.with_for_update(of=table)
    row = connection.execute(query).mappings().first()
    if row is None:
        raise unavailable_resource()
    return row


def published_chunks() -> FromClause:
    """Chunks joined to the active published generation of a live document and source."""
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


def published_scope(
    workspace_id: UUID, source_ids: tuple[UUID, ...]
) -> tuple[ColumnElement[bool], ...]:
    return (
        chunks.c.workspace_id == workspace_id,
        chunks.c.source_id.in_(source_ids),
        documents.c.deleted_at.is_(None),
        sources.c.deleted_at.is_(None),
        sources.c.workspace_id == workspace_id,
        index_generations.c.state == "published",
    )
