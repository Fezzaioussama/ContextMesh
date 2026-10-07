"""Scoped source mutations; each canonical change commits with its durable job."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, RowMapping, insert, select, update

from app.core.exceptions import unavailable_resource
from app.core.security import Identity
from app.db.models.knowledge import document_versions, documents, jobs, sources
from app.db.repositories.access import require_membership
from app.db.repositories.document_records import (
    INDEX_JOB,
    document_summary,
    documents_in_source,
    source_listing,
    source_value,
)
from app.db.repositories.job_repository import enqueue, job_value
from app.db.repositories.knowledge_access import require_document, require_editor, require_source
from app.domain.knowledge import DocumentSummary, JobView, Source, UploadReceipt
from app.domain.uploads import UploadSpec

RETRYABLE_STATES = ("failed", "cancelled")


class SourceRepository:
    def __init__(self, engine: Engine):
        self._engine = engine

    def create(self, identity: Identity, name: str, description: str) -> Source:
        now = datetime.now(UTC)
        source = Source(uuid4(), "upload", name, description, 0, 0, now)
        with self._engine.begin() as connection:
            require_editor(connection, identity)
            connection.execute(
                insert(sources).values(
                    id=source.id,
                    workspace_id=identity.workspace_id,
                    kind=source.kind,
                    name=name,
                    description=description,
                    owner_subject=identity.subject,
                    created_at=now,
                )
            )
        return source

    def sources(self, identity: Identity) -> tuple[Source, ...]:
        with self._engine.begin() as connection:
            require_membership(connection, identity)
            rows = connection.execute(source_listing(identity.workspace_id)).mappings()
            return tuple(source_value(row) for row in rows)

    def documents(self, identity: Identity, source_id: UUID) -> tuple[DocumentSummary, ...]:
        with self._engine.begin() as connection:
            require_source(connection, identity, source_id)
            return documents_in_source(connection, source_id)

    def register_upload(
        self, identity: Identity, source_id: UUID, spec: UploadSpec, digest: str, blob_key: str
    ) -> UploadReceipt:
        with self._engine.begin() as connection:
            require_editor(connection, identity)
            require_source(connection, identity, source_id, lock=True)
            existing = _live_document(connection, source_id, spec.external_id)
            unchanged = _unchanged_job(connection, existing, digest)
            if unchanged is not None:
                return UploadReceipt(document_summary(connection, existing["id"]), unchanged, True)
            return _new_version(connection, identity, source_id, existing, spec, digest, blob_key)

    def delete_document(self, identity: Identity, document_id: UUID) -> UUID:
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            require_editor(connection, identity)
            row = require_document(connection, identity, document_id, lock=True)
            connection.execute(
                update(documents)
                .where(documents.c.id == document_id)
                .values(deleted_at=now, updated_at=now)
            )
            return enqueue(
                connection,
                workspace_id=identity.workspace_id,
                kind="document.delete_requested",
                source_id=row["source_id"],
                document_id=document_id,
            )

    def delete_source(self, identity: Identity, source_id: UUID) -> UUID:
        with self._engine.begin() as connection:
            require_editor(connection, identity)
            require_source(connection, identity, source_id, lock=True)
            connection.execute(
                update(sources)
                .where(sources.c.id == source_id)
                .values(deleted_at=datetime.now(UTC))
            )
            return enqueue(
                connection,
                workspace_id=identity.workspace_id,
                kind="source.delete_requested",
                source_id=source_id,
            )

    def job(self, identity: Identity, job_id: UUID) -> JobView:
        query = select(jobs).where(
            jobs.c.id == job_id, jobs.c.workspace_id == identity.workspace_id
        )
        with self._engine.begin() as connection:
            require_membership(connection, identity)
            row = connection.execute(query).mappings().first()
        if row is None:
            raise unavailable_resource()
        return job_value(row)


def _live_document(connection: Connection, source_id: UUID, external_id: str) -> RowMapping | None:
    query = (
        select(documents)
        .where(
            documents.c.source_id == source_id,
            documents.c.external_id == external_id,
            documents.c.deleted_at.is_(None),
        )
        .with_for_update()
    )
    return connection.execute(query).mappings().first()


def _unchanged_job(connection: Connection, existing: RowMapping | None, digest: str) -> UUID | None:
    """Identical content is idempotent unless its last indexing attempt did not succeed."""
    if existing is None or existing["latest_version_id"] is None:
        return None
    query = (
        select(jobs.c.id)
        .select_from(
            document_versions.join(jobs, jobs.c.document_version_id == document_versions.c.id)
        )
        .where(
            document_versions.c.id == existing["latest_version_id"],
            document_versions.c.content_hash == digest,
            jobs.c.kind == INDEX_JOB,
            jobs.c.status.not_in(RETRYABLE_STATES),
        )
        .order_by(jobs.c.created_at.desc())
        .limit(1)
    )
    return connection.execute(query).scalar()


def _new_version(
    connection: Connection,
    identity: Identity,
    source_id: UUID,
    existing: RowMapping | None,
    spec: UploadSpec,
    digest: str,
    blob_key: str,
) -> UploadReceipt:
    now = datetime.now(UTC)
    document_id = _document_id(connection, identity, source_id, existing, spec, now)
    version_id = uuid4()
    connection.execute(
        insert(document_versions).values(
            id=version_id,
            document_id=document_id,
            content_hash=digest,
            blob_key=blob_key,
            byte_size=spec.byte_size,
            created_at=now,
        )
    )
    connection.execute(
        update(documents)
        .where(documents.c.id == document_id)
        .values(latest_version_id=version_id, title=spec.title, updated_at=now)
    )
    job_id = enqueue(
        connection,
        workspace_id=identity.workspace_id,
        kind=INDEX_JOB,
        source_id=source_id,
        document_id=document_id,
        version_id=version_id,
    )
    return UploadReceipt(document_summary(connection, document_id), job_id, False)


def _document_id(
    connection: Connection,
    identity: Identity,
    source_id: UUID,
    existing: RowMapping | None,
    spec: UploadSpec,
    now: datetime,
) -> UUID:
    if existing is not None:
        return existing["id"]
    document_id = uuid4()
    connection.execute(
        insert(documents).values(
            id=document_id,
            workspace_id=identity.workspace_id,
            source_id=source_id,
            external_id=spec.external_id,
            title=spec.title,
            media_type=spec.media_type,
            created_at=now,
            updated_at=now,
        )
    )
    return document_id
