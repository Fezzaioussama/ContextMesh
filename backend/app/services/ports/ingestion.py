"""Worker-side ports: fenced jobs, canonical staging/publication, parsing, vector writes."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.domain.knowledge import ChunkDraft, ClaimedJob, Element, IndexTarget


@dataclass(frozen=True)
class VectorPoint:
    chunk_id: UUID
    vector: tuple[float, ...]
    workspace_id: UUID
    source_id: UUID
    document_id: UUID
    generation_id: UUID


class JobQueue(Protocol):
    def claim(self, worker_id: str) -> ClaimedJob | None: ...

    def heartbeat(self, job: ClaimedJob, stage: str) -> None: ...

    def succeed(self, job: ClaimedJob) -> None: ...

    def cancel(self, job: ClaimedJob, code: str) -> None: ...

    def fail(self, job: ClaimedJob, code: str, retryable: bool) -> None: ...


class IndexingStore(Protocol):
    def target(self, job: ClaimedJob) -> IndexTarget | None: ...

    def stage(
        self,
        job: ClaimedJob,
        target: IndexTarget,
        signature: str,
        chunks: tuple[ChunkDraft, ...],
    ) -> UUID: ...

    def publish(self, job: ClaimedJob, target: IndexTarget, generation_id: UUID) -> UUID | None: ...

    def blob_keys(self, job: ClaimedJob) -> tuple[str, ...]: ...


class Parser(Protocol):
    def parse(self, content: bytes) -> tuple[Element, ...]: ...


class VectorWriter(Protocol):
    def upsert(self, points: Sequence[VectorPoint]) -> None: ...

    def count_generation(self, generation_id: UUID) -> int: ...

    def delete_generation(self, generation_id: UUID) -> None: ...

    def delete_document(self, document_id: UUID) -> None: ...

    def delete_source(self, source_id: UUID) -> None: ...
