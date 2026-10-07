"""Fenced crawl persistence: page versions, and retirement after a complete scan only."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Connection, Engine, select, update

from app.db.models.knowledge import documents, sources
from app.db.repositories.job_repository import enqueue, fenced, finished, require_lease
from app.db.repositories.version_records import record_version
from app.domain.knowledge import ClaimedJob, WebsiteTarget
from app.domain.uploads import UploadSpec


class CrawlRepository:
    def __init__(self, engine: Engine):
        self._engine = engine

    def website(self, job: ClaimedJob) -> WebsiteTarget | None:
        query = select(sources).where(
            sources.c.id == job.source_id,
            sources.c.kind == "website",
            sources.c.deleted_at.is_(None),
        )
        with self._engine.begin() as connection:
            row = connection.execute(query).mappings().first()
        if row is None:
            return None
        return WebsiteTarget(row["workspace_id"], row["id"], row["url"])

    def register_page(
        self, job: ClaimedJob, target: WebsiteTarget, spec: UploadSpec, digest: str, blob_key: str
    ) -> bool:
        """True when a new version was recorded; a deleted source records nothing."""
        with self._engine.begin() as connection:
            require_lease(connection, job)
            if not _live_source(connection, target.source_id):
                return False
            recorded = record_version(
                connection, target.workspace_id, target.source_id, spec, digest, blob_key
            )
            return not recorded.duplicate

    def finish(
        self, job: ClaimedJob, target: WebsiteTarget, seen: frozenset[str], covered: str | None
    ) -> int:
        """A partial crawl (`covered` is None) keeps every page it did not see."""
        with self._engine.begin() as connection:
            require_lease(connection, job)
            if not _live_source(connection, target.source_id):
                fenced(connection, job, **finished(status="cancelled", safe_error_code="obsolete"))
                return 0
            retired = 0 if covered is None else _retire_missing(connection, target, seen, covered)
            stage = "crawled_partial" if covered is None else "crawled"
            fenced(connection, job, **finished(status="succeeded", stage=stage))
            return retired


def _live_source(connection: Connection, source_id: UUID) -> bool:
    """Share-lock the source so a concurrent deletion cannot interleave with this write."""
    query = select(sources.c.deleted_at).where(sources.c.id == source_id).with_for_update(read=True)
    row = connection.execute(query).first()
    return row is not None and row.deleted_at is None


def _retire_missing(
    connection: Connection, target: WebsiteTarget, seen: frozenset[str], covered: str
) -> int:
    """Pages absent from a complete crawl of their part of the site are gone: hide them
    now, clean up in the worker. Pages outside what the crawl covered are kept."""
    query = select(documents.c.id).where(
        documents.c.source_id == target.source_id,
        documents.c.deleted_at.is_(None),
        documents.c.external_id.startswith(covered, autoescape=True),
        documents.c.external_id.not_in(seen),
    )
    missing = list(connection.execute(query).scalars())
    for document_id in missing:
        _retire(connection, target, document_id)
    return len(missing)


def _retire(connection: Connection, target: WebsiteTarget, document_id: UUID) -> None:
    now = datetime.now(UTC)
    connection.execute(
        update(documents)
        .where(documents.c.id == document_id)
        .values(deleted_at=now, updated_at=now)
    )
    enqueue(
        connection,
        workspace_id=target.workspace_id,
        kind="document.delete_requested",
        source_id=target.source_id,
        document_id=document_id,
    )
