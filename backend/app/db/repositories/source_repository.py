"""Scoped source mutations; each canonical change commits with its durable job."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, RowMapping, insert, select, update

from app.core.exceptions import unavailable_resource
from app.core.security import Identity
from app.db.models.knowledge import documents, jobs, sources
from app.db.repositories.access import require_membership
from app.db.repositories.document_records import (
    document_summary,
    documents_in_source,
    source_listing,
    source_value,
)
from app.db.repositories.job_repository import enqueue, job_value
from app.db.repositories.knowledge_access import require_document, require_editor, require_source
from app.db.repositories.version_records import record_version
from app.domain.errors import wrong_source_kind
from app.domain.knowledge import DocumentSummary, JobView, Source, SourceDraft, UploadReceipt
from app.domain.uploads import UploadSpec

SYNC_JOB = "source.sync_requested"
OPEN_STATES = ("queued", "running")


class SourceRepository:
    def __init__(self, engine: Engine):
        self._engine = engine

    def create(self, identity: Identity, draft: SourceDraft) -> Source:
        now = datetime.now(UTC)
        source = Source(uuid4(), draft.kind, draft.name, draft.description, 0, 0, now, draft.url)
        with self._engine.begin() as connection:
            require_editor(connection, identity)
            connection.execute(
                insert(sources).values(
                    id=source.id,
                    workspace_id=identity.workspace_id,
                    kind=draft.kind,
                    name=draft.name,
                    description=draft.description,
                    url=draft.url,
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
            _require_kind(_editable_source(connection, identity, source_id), "upload")
            recorded = record_version(
                connection, identity.workspace_id, source_id, spec, digest, blob_key
            )
            summary = document_summary(connection, recorded.document_id)
            return UploadReceipt(summary, recorded.job_id, recorded.duplicate)

    def request_sync(self, identity: Identity, source_id: UUID) -> UUID:
        """At most one open crawl per source; a repeated request returns the open job."""
        with self._engine.begin() as connection:
            _require_kind(_editable_source(connection, identity, source_id), "website")
            return _open_sync(connection, source_id) or enqueue(
                connection,
                workspace_id=identity.workspace_id,
                kind=SYNC_JOB,
                source_id=source_id,
            )

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
            _editable_source(connection, identity, source_id)
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


def _editable_source(connection: Connection, identity: Identity, source_id: UUID) -> RowMapping:
    require_editor(connection, identity)
    return require_source(connection, identity, source_id, lock=True)


def _require_kind(source: RowMapping, kind: str) -> None:
    if source["kind"] != kind:
        raise wrong_source_kind()


def _open_sync(connection: Connection, source_id: UUID) -> UUID | None:
    query = select(jobs.c.id).where(
        jobs.c.source_id == source_id, jobs.c.kind == SYNC_JOB, jobs.c.status.in_(OPEN_STATES)
    )
    return connection.execute(query.limit(1)).scalar()
