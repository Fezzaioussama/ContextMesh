"""Index one immutable document version and publish it only through fenced checks."""

import logging
from collections.abc import Mapping
from uuid import UUID

from app.services.ports.ingestion import (
    IndexingStore,
    JobQueue,
    Parser,
    VectorPoint,
    VectorWriter,
)
from app.services.ports.models import EmbeddingModel
from app.services.ports.sources import BlobStore
from app.services.rules.chunking import ChunkingPolicy, chunk_elements
from app.services.rules.errors import IngestionFailure, VectorIndexUnavailable
from app.services.rules.knowledge import ChunkDraft, ClaimedJob, IndexTarget
from app.utils.logging import flow_event, job_flow

PARSER_REVISION = "parser-v2"
logger = logging.getLogger("context_mesh.worker")


class DocumentIndexer:
    def __init__(
        self,
        jobs: JobQueue,
        store: IndexingStore,
        blobs: BlobStore,
        parsers: Mapping[str, Parser],
        embeddings: EmbeddingModel,
        vectors: VectorWriter,
        *,
        policy: ChunkingPolicy = ChunkingPolicy(),
        batch_size: int = 64,
    ):
        self._jobs = jobs
        self._store = store
        self._blobs = blobs
        self._parsers = parsers
        self._embeddings = embeddings
        self._vectors = vectors
        self._policy = policy
        self._batch_size = batch_size

    @property
    def signature(self) -> str:
        return f"{PARSER_REVISION}|{self._policy.signature}|{self._embeddings.identity}"

    def handle(self, job: ClaimedJob) -> None:
        target = self._store.target(job)
        if target is None:
            self._jobs.cancel(job, "obsolete")
            flow_event(job_flow(job.kind), "job_cancelled", code="obsolete")
            return
        chunks = self._chunks(job, target)
        generation_id = self._store.stage(job, target, self.signature, chunks)
        self._index(job, target, generation_id, chunks)
        discarded = self._store.publish(job, target, generation_id)
        if discarded == generation_id:  # Deleted or replaced while indexing: not published.
            flow_event(job_flow(job.kind), "job_cancelled", code="obsolete")
        self._discard(discarded)

    def _chunks(self, job: ClaimedJob, target: IndexTarget) -> tuple[ChunkDraft, ...]:
        self._jobs.heartbeat(job, "parsing")
        elements = self._parser(target.media_type).parse(self._content(target))
        seed = f"{target.document_id}:{target.document_version_id}:{self.signature}"
        chunks = chunk_elements(elements, self._policy, seed)
        if not chunks:
            raise IngestionFailure("no_text")
        return chunks

    def _parser(self, media_type: str) -> Parser:
        parser = self._parsers.get(media_type)
        if parser is None:
            raise IngestionFailure("unsupported_media_type")
        return parser

    def _content(self, target: IndexTarget) -> bytes:
        try:
            return self._blobs.read(target.blob_key)
        except LookupError:
            raise IngestionFailure("content_unavailable") from None

    def _index(
        self,
        job: ClaimedJob,
        target: IndexTarget,
        generation_id: UUID,
        chunks: tuple[ChunkDraft, ...],
    ) -> None:
        for start in range(0, len(chunks), self._batch_size):
            self._jobs.heartbeat(job, "embedding")
            self._write_batch(target, generation_id, chunks[start : start + self._batch_size])
        self._jobs.heartbeat(job, "verifying")
        if self._vectors.count_generation(generation_id) != len(chunks):
            raise IngestionFailure("index_verification_failed", retryable=True)

    def _write_batch(
        self, target: IndexTarget, generation_id: UUID, batch: tuple[ChunkDraft, ...]
    ) -> None:
        vectors = self._embeddings.embed([chunk.embedding_text for chunk in batch])
        self._vectors.upsert(
            [
                VectorPoint(
                    chunk.id,
                    vector,
                    target.workspace_id,
                    target.source_id,
                    target.document_id,
                    generation_id,
                )
                for chunk, vector in zip(batch, vectors, strict=True)
            ]
        )

    def _discard(self, previous: UUID | None) -> None:
        if previous is None:
            return
        try:
            self._vectors.delete_generation(previous)
        except VectorIndexUnavailable:
            logger.warning("Deferred vector cleanup; generation_id=%s", previous)
