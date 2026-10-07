"""Worker-side canonical writes: replay-safe staging and fenced atomic publication."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, RowMapping, select, update
from sqlalchemy.dialects.postgresql import insert

from app.db.models.knowledge import (
    chunks,
    document_versions,
    documents,
    index_generations,
    sources,
)
from app.db.repositories.job_repository import fenced, finished, require_lease
from app.domain.knowledge import ChunkDraft, ClaimedJob, IndexTarget


class IndexingRepository:
    def __init__(self, engine: Engine):
        self._engine = engine

    def target(self, job: ClaimedJob) -> IndexTarget | None:
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
        if not _current(row):
            return None
        return _target(row)

    def stage(
        self,
        job: ClaimedJob,
        target: IndexTarget,
        signature: str,
        chunk_drafts: tuple[ChunkDraft, ...],
    ) -> UUID:
        with self._engine.begin() as connection:
            require_lease(connection, job)
            generation_id = _generation(connection, target, signature, len(chunk_drafts))
            _insert_chunks(connection, target, generation_id, chunk_drafts)
        return generation_id

    def publish(self, job: ClaimedJob, target: IndexTarget, generation_id: UUID) -> UUID | None:
        """Return the generation whose vectors are now obsolete, if any."""
        with self._engine.begin() as connection:
            require_lease(connection, job)
            document = _locked_document(connection, target.document_id)
            if not _publishable(document, target):
                fenced(connection, job, **finished(status="cancelled", safe_error_code="obsolete"))
                return generation_id
            previous = _activate(connection, document, generation_id)
            fenced(connection, job, **finished(status="succeeded", stage="published"))
        return previous

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


def _current(row: RowMapping | None) -> bool:
    if row is None:
        return False
    live = row["deleted_at"] is None and row["source_deleted_at"] is None
    return live and row["latest_version_id"] == row["version_id"]


def _target(row: RowMapping) -> IndexTarget:
    return IndexTarget(
        row["workspace_id"],
        row["source_id"],
        row["document_id"],
        row["version_id"],
        row["blob_key"],
        row["media_type"],
        row["title"],
    )


def _generation(connection: Connection, target: IndexTarget, signature: str, expected: int) -> UUID:
    statement = (
        insert(index_generations)
        .values(
            id=uuid4(),
            document_id=target.document_id,
            document_version_id=target.document_version_id,
            pipeline_signature=signature,
            state="pending",
            expected_chunks=expected,
            created_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(constraint="uq_generation_target")
    )
    connection.execute(statement)
    query = select(index_generations.c.id).where(
        index_generations.c.document_version_id == target.document_version_id,
        index_generations.c.pipeline_signature == signature,
    )
    return connection.execute(query).scalar_one()


def _insert_chunks(
    connection: Connection,
    target: IndexTarget,
    generation_id: UUID,
    chunk_drafts: tuple[ChunkDraft, ...],
) -> None:
    """Deterministic chunk IDs make a replayed stage insert nothing new."""
    if not chunk_drafts:
        return
    rows = [_chunk_row(target, generation_id, chunk) for chunk in chunk_drafts]
    connection.execute(insert(chunks).on_conflict_do_nothing(), rows)


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


def _publishable(document: RowMapping, target: IndexTarget) -> bool:
    live = document["deleted_at"] is None and document["source_deleted_at"] is None
    return live and document["latest_version_id"] == target.document_version_id


def _activate(connection: Connection, document: RowMapping, generation_id: UUID) -> UUID | None:
    previous = document["active_generation_id"]
    if previous == generation_id:
        return None
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
        .where(documents.c.id == document["id"])
        .values(active_generation_id=generation_id, updated_at=now)
    )
    return previous
