"""Record a document version and its index job in the caller's transaction.

Shared by uploads and crawled pages: identical content is idempotent unless its last
indexing attempt failed, and a new version always enqueues indexing atomically.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Connection, RowMapping, insert, select, update

from app.data.db.models.knowledge import document_versions, documents, jobs
from app.data.db.repositories.job_repository import enqueue
from app.services.rules.uploads import UploadSpec

INDEX_JOB = "document.index_requested"
RETRYABLE_STATES = ("failed", "cancelled")


@dataclass(frozen=True)
class RecordedVersion:
    document_id: UUID
    job_id: UUID
    duplicate: bool


def record_version(
    connection: Connection,
    workspace_id: UUID,
    source_id: UUID,
    spec: UploadSpec,
    digest: str,
    blob_key: str,
) -> RecordedVersion:
    existing = _live_document(connection, source_id, spec.external_id)
    unchanged = _unchanged_job(connection, existing, digest)
    if unchanged is not None:
        return RecordedVersion(existing["id"], unchanged, True)
    document_id = _document_id(connection, workspace_id, source_id, existing, spec)
    job_id = _new_version(connection, workspace_id, source_id, document_id, spec, digest, blob_key)
    return RecordedVersion(document_id, job_id, False)


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


def _document_id(
    connection: Connection,
    workspace_id: UUID,
    source_id: UUID,
    existing: RowMapping | None,
    spec: UploadSpec,
) -> UUID:
    if existing is not None:
        return existing["id"]
    document_id = uuid4()
    now = datetime.now(UTC)
    connection.execute(
        insert(documents).values(
            id=document_id,
            workspace_id=workspace_id,
            source_id=source_id,
            external_id=spec.external_id,
            title=spec.title,
            media_type=spec.media_type,
            source_uri=spec.uri,
            created_at=now,
            updated_at=now,
        )
    )
    return document_id


def _new_version(
    connection: Connection,
    workspace_id: UUID,
    source_id: UUID,
    document_id: UUID,
    spec: UploadSpec,
    digest: str,
    blob_key: str,
) -> UUID:
    now = datetime.now(UTC)
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
        .values(
            latest_version_id=version_id,
            title=spec.title,
            media_type=spec.media_type,
            source_uri=spec.uri,
            updated_at=now,
        )
    )
    return enqueue(
        connection,
        workspace_id=workspace_id,
        kind=INDEX_JOB,
        source_id=source_id,
        document_id=document_id,
        version_id=version_id,
    )
