"""Sources and documents: scoped listings and mutations, each committed with its job.

Deleting hides content at once (a tombstone); the worker removes vectors and blobs later.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, RowMapping, Select, and_, func, insert, select, update

from app.data.db import chunks, document_versions, documents, jobs, member_role, sources
from app.data.jobs import enqueue, job_view
from app.services.models import (
    DocumentSummary,
    Identity,
    JobView,
    Source,
    SourceKind,
    UploadReceipt,
    UploadSpec,
)
from app.utils.errors import ContextMeshError

INDEX_JOB = "document.index_requested"
SYNC_JOB = "source.sync_requested"
EDITOR_ROLES = frozenset({"owner", "editor"})


class SourceRepository:
    def __init__(self, engine: Engine):
        self._engine = engine

    def create(
        self, identity: Identity, kind: SourceKind, name: str, description: str, url: str | None
    ) -> Source:
        now = datetime.now(UTC)
        source = Source(uuid4(), kind, name, description, 0, 0, now, url)
        with self._engine.begin() as connection:
            _require_editor(connection, identity)
            connection.execute(
                insert(sources).values(
                    id=source.id,
                    workspace_id=identity.workspace_id,
                    kind=kind,
                    name=name,
                    description=description,
                    url=url,
                    owner_subject=identity.subject,
                    created_at=now,
                )
            )
        return source

    def sources(self, identity: Identity) -> tuple[Source, ...]:
        live = and_(documents.c.source_id == sources.c.id, documents.c.deleted_at.is_(None))
        sync = jobs.alias("sync_jobs")
        query = (
            select(
                sources,
                func.count(documents.c.id).label("document_count"),
                func.count(documents.c.active_generation_id).label("searchable_count"),
                *_job_columns(sync),
            )
            .select_from(
                sources.outerjoin(documents, live).outerjoin(
                    sync, sync.c.id == _latest_job("source_id", SYNC_JOB, sources)
                )
            )
            .where(sources.c.workspace_id == identity.workspace_id, sources.c.deleted_at.is_(None))
            .group_by(sources.c.id, *sync.c)
            .order_by(sources.c.created_at, sources.c.id)
            .limit(200)
        )
        with self._engine.begin() as connection:
            member_role(connection, identity)
            rows = connection.execute(query).mappings().all()
        return tuple(
            Source(
                row["id"],
                row["kind"],
                row["name"],
                row["description"],
                row["document_count"],
                row["searchable_count"],
                row["created_at"],
                row["url"],
                _latest_job_view(row),
            )
            for row in rows
        )

    def documents(self, identity: Identity, source_id: UUID) -> tuple[DocumentSummary, ...]:
        query = (
            _document_listing()
            .where(documents.c.source_id == source_id, documents.c.deleted_at.is_(None))
            .order_by(documents.c.updated_at.desc(), documents.c.id)
            .limit(500)
        )
        with self._engine.begin() as connection:
            _require_source(connection, identity, source_id)
            rows = connection.execute(query).mappings().all()
        return tuple(_document_summary(row) for row in rows)

    def register_upload(
        self, identity: Identity, source_id: UUID, spec: UploadSpec, digest: str, blob_key: str
    ) -> UploadReceipt:
        with self._engine.begin() as connection:
            _require_kind(_editable_source(connection, identity, source_id), "upload")
            recorded = record_version(
                connection, identity.workspace_id, source_id, spec, digest, blob_key
            )
            query = _document_listing().where(documents.c.id == recorded.document_id)
            summary = _document_summary(connection.execute(query).mappings().one())
        return UploadReceipt(summary, recorded.job_id, recorded.duplicate)

    def request_sync(self, identity: Identity, source_id: UUID) -> UUID:
        """At most one open crawl per source; a repeated request returns the open job."""
        open_sync = select(jobs.c.id).where(
            jobs.c.source_id == source_id,
            jobs.c.kind == SYNC_JOB,
            jobs.c.status.in_(("queued", "running")),
        )
        with self._engine.begin() as connection:
            _require_kind(_editable_source(connection, identity, source_id), "website")
            return connection.execute(open_sync.limit(1)).scalar() or enqueue(
                connection, workspace_id=identity.workspace_id, kind=SYNC_JOB, source_id=source_id
            )

    def delete_document(self, identity: Identity, document_id: UUID) -> UUID:
        now = datetime.now(UTC)
        query = (
            select(documents)
            .join(sources, sources.c.id == documents.c.source_id)
            .where(
                documents.c.id == document_id,
                documents.c.workspace_id == identity.workspace_id,
                documents.c.deleted_at.is_(None),
                sources.c.deleted_at.is_(None),
            )
            .with_for_update(of=documents)
        )
        with self._engine.begin() as connection:
            _require_editor(connection, identity)
            row = connection.execute(query).mappings().first()
            if row is None:
                raise ContextMeshError("not_found")
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
            member_role(connection, identity)
            row = connection.execute(query).mappings().first()
        if row is None:
            raise ContextMeshError("not_found")
        return job_view(row)


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
    """Record a new document version and enqueue its indexing in the caller's transaction.

    Shared by uploads and crawled pages. Identical content is a duplicate (no new
    version or job) unless its last indexing attempt failed or was cancelled.
    """
    now = datetime.now(UTC)
    existing = (
        connection.execute(
            select(documents)
            .where(
                documents.c.source_id == source_id,
                documents.c.external_id == spec.external_id,
                documents.c.deleted_at.is_(None),
            )
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if existing is not None and existing["latest_version_id"] is not None:
        unchanged = (
            select(jobs.c.id)
            .select_from(
                document_versions.join(jobs, jobs.c.document_version_id == document_versions.c.id)
            )
            .where(
                document_versions.c.id == existing["latest_version_id"],
                document_versions.c.content_hash == digest,
                jobs.c.kind == INDEX_JOB,
                jobs.c.status.not_in(("failed", "cancelled")),
            )
            .order_by(jobs.c.created_at.desc())
            .limit(1)
        )
        job_id = connection.execute(unchanged).scalar()
        if job_id is not None:
            return RecordedVersion(existing["id"], job_id, True)
    if existing is None:
        document_id = uuid4()
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
    else:
        document_id = existing["id"]
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
    job_id = enqueue(
        connection,
        workspace_id=workspace_id,
        kind=INDEX_JOB,
        source_id=source_id,
        document_id=document_id,
        version_id=version_id,
    )
    return RecordedVersion(document_id, job_id, False)


# Access


def _require_editor(connection: Connection, identity: Identity) -> None:
    if member_role(connection, identity) not in EDITOR_ROLES:
        raise ContextMeshError("forbidden")


def _require_source(
    connection: Connection, identity: Identity, source_id: UUID, lock: bool = False
) -> RowMapping:
    member_role(connection, identity)
    query = select(sources).where(
        sources.c.id == source_id,
        sources.c.workspace_id == identity.workspace_id,
        sources.c.deleted_at.is_(None),
    )
    if lock:
        query = query.with_for_update(of=sources)
    row = connection.execute(query).mappings().first()
    if row is None:
        raise ContextMeshError("not_found")
    return row


def _editable_source(connection: Connection, identity: Identity, source_id: UUID) -> RowMapping:
    _require_editor(connection, identity)
    return _require_source(connection, identity, source_id, lock=True)


def _require_kind(source: RowMapping, kind: str) -> None:
    if source["kind"] != kind:
        raise ContextMeshError("wrong_source_kind")


# Listings with derived searchability and the latest job


def _latest_job(owner: str, kind: str, correlate):
    """The newest job of a kind for the outer row; an alias avoids self-correlation."""
    candidates = jobs.alias("candidate_jobs")
    return (
        select(candidates.c.id)
        .where(candidates.c[owner] == correlate.c.id, candidates.c.kind == kind)
        .order_by(candidates.c.created_at.desc())
        .limit(1)
        .correlate(correlate)
        .scalar_subquery()
    )


def _job_columns(table) -> list:
    return [
        table.c.id.label("job_id"),
        table.c.kind.label("job_kind"),
        table.c.status.label("job_status"),
        table.c.attempts.label("job_attempts"),
        table.c.stage.label("job_stage"),
        table.c.safe_error_code.label("job_error"),
        table.c.updated_at.label("job_updated_at"),
    ]


def _latest_job_view(row: RowMapping) -> JobView | None:
    if row["job_id"] is None:
        return None
    return JobView(
        row["job_id"],
        row["job_kind"],
        row["job_status"],
        row["job_attempts"],
        row["job_stage"],
        row["job_error"],
        row["job_updated_at"],
    )


def _document_listing() -> Select:
    chunk_count = (
        select(func.count(chunks.c.id))
        .where(chunks.c.generation_id == documents.c.active_generation_id)
        .correlate(documents)
        .scalar_subquery()
    )
    latest_job = _latest_job("document_id", INDEX_JOB, documents)
    return select(
        documents.c.id,
        documents.c.source_id,
        documents.c.title,
        documents.c.media_type,
        documents.c.active_generation_id,
        documents.c.updated_at,
        documents.c.source_uri,
        chunk_count.label("chunk_count"),
        *_job_columns(jobs),
    ).select_from(documents.outerjoin(jobs, jobs.c.id == latest_job))


def _document_summary(row: RowMapping) -> DocumentSummary:
    return DocumentSummary(
        row["id"],
        row["source_id"],
        row["title"],
        row["media_type"],
        row["active_generation_id"] is not None,
        row["chunk_count"],
        _latest_job_view(row),
        row["updated_at"],
        row["source_uri"],
    )
