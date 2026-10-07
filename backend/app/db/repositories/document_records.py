"""Source and document listings with derived searchability and latest job state."""

from uuid import UUID

from sqlalchemy import Connection, RowMapping, Select, and_, func, select

from app.db.models.knowledge import chunks, documents, jobs, sources
from app.domain.knowledge import DocumentSummary, JobView, Source

INDEX_JOB = "document.index_requested"


def source_listing(workspace_id: UUID) -> Select:
    live = and_(documents.c.source_id == sources.c.id, documents.c.deleted_at.is_(None))
    return (
        select(
            sources,
            func.count(documents.c.id).label("document_count"),
            func.count(documents.c.active_generation_id).label("searchable_count"),
        )
        .select_from(sources.outerjoin(documents, live))
        .where(sources.c.workspace_id == workspace_id, sources.c.deleted_at.is_(None))
        .group_by(sources.c.id)
        .order_by(sources.c.created_at, sources.c.id)
        .limit(200)
    )


def source_value(row: RowMapping) -> Source:
    return Source(
        row["id"],
        row["kind"],
        row["name"],
        row["description"],
        row["document_count"],
        row["searchable_count"],
        row["created_at"],
    )


def document_listing() -> Select:
    candidates = jobs.alias("candidate_jobs")
    latest_job = (
        select(candidates.c.id)
        .where(candidates.c.document_id == documents.c.id, candidates.c.kind == INDEX_JOB)
        .order_by(candidates.c.created_at.desc())
        .limit(1)
        .correlate(documents)
        .scalar_subquery()
    )
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
        chunk_count.label("chunk_count"),
        jobs.c.id.label("job_id"),
        jobs.c.kind.label("job_kind"),
        jobs.c.status.label("job_status"),
        jobs.c.attempts.label("job_attempts"),
        jobs.c.stage.label("job_stage"),
        jobs.c.safe_error_code.label("job_error"),
        jobs.c.updated_at.label("job_updated_at"),
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
        _latest_job(row),
        row["updated_at"],
    )


def _latest_job(row: RowMapping) -> JobView | None:
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
