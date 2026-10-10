"""Worker-side writes: replay-safe chunk staging, fenced publication, and crawl results.

Every write first checks the job's lease (`require_lease`), so a worker that lost its
job can never publish or retire anything.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, RowMapping, select, update
from sqlalchemy.dialects.postgresql import insert

from app.data.db import chunks, document_versions, documents, index_generations, sources
from app.data.jobs import enqueue, fenced, finished, require_lease
from app.data.sources import record_version
from app.services.models import ChunkDraft, ClaimedJob, IndexTarget, UploadSpec, WebsiteTarget


class IndexingRepository:
    def __init__(self, engine: Engine):
        self._engine = engine

    def target(self, job: ClaimedJob) -> IndexTarget | None:
        """The version to index, or None if it was deleted or replaced meanwhile."""
        query = (
            select(
                documents.c.workspace_id,
                documents.c.source_id,
                documents.c.id.label("document_id"),
                documents.c.latest_version_id,
                documents.c.deleted_at,
                documents.c.media_type,
                documents.c.title,
                document_versions.c.id.label("version_id"),
                document_versions.c.blob_key,
                sources.c.deleted_at.label("source_deleted_at"),
            )
            .select_from(
                document_versions.join(
                    documents, documents.c.id == document_versions.c.document_id
                ).join(sources, sources.c.id == documents.c.source_id)
            )
            .where(document_versions.c.id == job.document_version_id)
        )
        with self._engine.begin() as connection:
            row = connection.execute(query).mappings().first()
        if row is None or not _current(row, row["version_id"]):
            return None
        return IndexTarget(
            row["workspace_id"],
            row["source_id"],
            row["document_id"],
            row["version_id"],
            row["blob_key"],
            row["media_type"],
            row["title"],
        )

    def stage(
        self,
        job: ClaimedJob,
        target: IndexTarget,
        signature: str,
        chunk_drafts: tuple[ChunkDraft, ...],
    ) -> UUID:
        """Create (or reuse) a pending generation; deterministic chunk ids make a replay
        insert nothing new."""
        with self._engine.begin() as connection:
            require_lease(connection, job)
            connection.execute(
                insert(index_generations)
                .values(
                    id=uuid4(),
                    document_id=target.document_id,
                    document_version_id=target.document_version_id,
                    pipeline_signature=signature,
                    state="pending",
                    expected_chunks=len(chunk_drafts),
                    created_at=datetime.now(UTC),
                )
                .on_conflict_do_nothing(constraint="uq_generation_target")
            )
            generation_id = connection.execute(
                select(index_generations.c.id).where(
                    index_generations.c.document_version_id == target.document_version_id,
                    index_generations.c.pipeline_signature == signature,
                )
            ).scalar_one()
            if chunk_drafts:
                rows = [_chunk_row(target, generation_id, chunk) for chunk in chunk_drafts]
                connection.execute(insert(chunks).on_conflict_do_nothing(), rows)
        return generation_id

    def publish(self, job: ClaimedJob, target: IndexTarget, generation_id: UUID) -> UUID | None:
        """Make the generation visible, unless the document was deleted or replaced.

        Returns the generation whose vectors are now obsolete: the previous one, or this
        one when it could not be published.
        """
        with self._engine.begin() as connection:
            require_lease(connection, job)
            document = _locked_document(connection, target.document_id)
            if not _current(document, target.document_version_id):
                fenced(connection, job, **finished(status="cancelled", safe_error_code="obsolete"))
                return generation_id
            previous = document["active_generation_id"]
            if previous != generation_id:
                _activate(connection, target.document_id, generation_id, previous)
            fenced(connection, job, **finished(status="succeeded", stage="published"))
        return None if previous == generation_id else previous

    def blob_keys(self, job: ClaimedJob) -> tuple[str, ...]:
        scope = documents.c.source_id == job.source_id
        if job.document_id is not None:
            scope = documents.c.id == job.document_id
        query = (
            select(document_versions.c.blob_key)
            .join(documents, documents.c.id == document_versions.c.document_id)
            .where(scope)
        )
        with self._engine.begin() as connection:
            return tuple(connection.execute(query).scalars())


def _current(row: RowMapping, version_id: UUID) -> bool:
    live = row["deleted_at"] is None and row["source_deleted_at"] is None
    return live and row["latest_version_id"] == version_id


def _chunk_row(target: IndexTarget, generation_id: UUID, chunk: ChunkDraft) -> dict[str, object]:
    return {
        "id": chunk.id,
        "generation_id": generation_id,
        "document_id": target.document_id,
        "source_id": target.source_id,
        "workspace_id": target.workspace_id,
        "ordinal": chunk.ordinal,
        "heading": " > ".join(chunk.locator.heading_path),
        "heading_path": list(chunk.locator.heading_path),
        "content": chunk.text,
        "line_start": chunk.locator.line_start,
        "line_end": chunk.locator.line_end,
        "page": chunk.locator.page,
        "slide": chunk.locator.slide,
        "token_count": chunk.token_count,
    }


def _locked_document(connection: Connection, document_id: UUID) -> RowMapping:
    """Share-lock the source first so a concurrent source deletion cannot interleave."""
    source = select(documents.c.source_id).where(documents.c.id == document_id).scalar_subquery()
    connection.execute(
        select(sources.c.id).where(sources.c.id == source).with_for_update(read=True)
    )
    query = (
        select(documents, sources.c.deleted_at.label("source_deleted_at"))
        .join(sources, sources.c.id == documents.c.source_id)
        .where(documents.c.id == document_id)
        .with_for_update(of=documents)
    )
    return connection.execute(query).mappings().one()


def _activate(
    connection: Connection, document_id: UUID, generation_id: UUID, previous: UUID | None
) -> None:
    now = datetime.now(UTC)
    connection.execute(
        update(index_generations)
        .where(index_generations.c.id == generation_id)
        .values(state="published", published_at=now)
    )
    connection.execute(
        update(index_generations)
        .where(index_generations.c.id == previous)
        .values(state="superseded")
    )
    connection.execute(
        update(documents)
        .where(documents.c.id == document_id)
        .values(active_generation_id=generation_id, updated_at=now)
    )


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
        """Succeed the crawl. Pages under the `covered` URL prefix that the crawl did not
        see are gone: hide them now, the worker cleans up later. A partial crawl
        (`covered` is None) retires nothing. Returns how many pages were retired."""
        with self._engine.begin() as connection:
            require_lease(connection, job)
            if not _live_source(connection, target.source_id):
                fenced(connection, job, **finished(status="cancelled", safe_error_code="obsolete"))
                return 0
            missing = [] if covered is None else _missing_pages(connection, target, seen, covered)
            now = datetime.now(UTC)
            for document_id in missing:
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
            stage = "crawled_partial" if covered is None else "crawled"
            fenced(connection, job, **finished(status="succeeded", stage=stage))
            return len(missing)


def _live_source(connection: Connection, source_id: UUID) -> bool:
    """Share-lock the source so a concurrent deletion cannot interleave with this write."""
    query = select(sources.c.deleted_at).where(sources.c.id == source_id).with_for_update(read=True)
    row = connection.execute(query).first()
    return row is not None and row.deleted_at is None


def _missing_pages(
    connection: Connection, target: WebsiteTarget, seen: frozenset[str], covered: str
) -> list[UUID]:
    query = select(documents.c.id).where(
        documents.c.source_id == target.source_id,
        documents.c.deleted_at.is_(None),
        documents.c.external_id.startswith(covered, autoescape=True),
        documents.c.external_id.not_in(seen),
    )
    return list(connection.execute(query).scalars())
