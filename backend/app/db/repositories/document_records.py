"""Source and document listings with derived searchability and latest job state."""

from uuid import UUID

from sqlalchemy import Connection, RowMapping, Select, and_, func, select

from app.db.models.knowledge import chunks, documents, jobs, sources
from app.domain.knowledge import DocumentSummary, JobView, Source

INDEX_JOB = "document.index_requested"
SYNC_JOB = "source.sync_requested"


def _latest_job(owner, kind: str, correlate):
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


def source_listing(workspace_id: UUID) -> Select:
    live = and_(documents.c.source_id == sources.c.id, documents.c.deleted_at.is_(None))
    sync = jobs.alias("sync_jobs")
    latest_sync = _latest_job("source_id", SYNC_JOB, sources)
    return (
        select(
            sources,
            func.count(documents.c.id).label("document_count"),
            func.count(documents.c.active_generation_id).label("searchable_count"),
            *_job_columns(sync),
        )
        .select_from(sources.outerjoin(documents, live).outerjoin(sync, sync.c.id == latest_sync))
        .where(sources.c.workspace_id == workspace_id, sources.c.deleted_at.is_(None))
        .group_by(sources.c.id, *sync.c)
        .order_by(sources.c.created_at, sources.c.id)
        .limit(200)
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


def source_value(row: RowMapping) -> Source:
    return Source(
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


def document_listing() -> Select:
    latest_job = _latest_job("document_id", INDEX_JOB, documents)
    chunk_count = (
        select(func.count(chunks.c.id))
        .where(chunks.c.generation_id == documents.c.active_generation_id)
        .correlate(documents)
        .scalar_subquery()
    )
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


def documents_in_source(connection: Connection, source_id: UUID) -> tuple[DocumentSummary, ...]:
    query = (
        document_listing()
        .where(documents.c.source_id == source_id, documents.c.deleted_at.is_(None))
        .order_by(documents.c.updated_at.desc(), documents.c.id)
        .limit(500)
    )
    return tuple(document_value(row) for row in connection.execute(query).mappings())


def document_summary(connection: Connection, document_id: UUID) -> DocumentSummary:
    query = document_listing().where(documents.c.id == document_id)
    return document_value(connection.execute(query).mappings().one())


def document_value(row: RowMapping) -> DocumentSummary:
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
