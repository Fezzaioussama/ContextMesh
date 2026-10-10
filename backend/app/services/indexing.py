"""The ingestion worker: claim one durable job at a time, run its handler, and record a
safe outcome for every failure.

Handlers: index a document version (parse, chunk, embed, publish), crawl a website
(`services/crawler.py`), and erase deleted documents or sources.
"""

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Protocol
from uuid import UUID, uuid5

from app.data.blobs import FilesystemBlobStore
from app.data.indexing import IndexingRepository
from app.data.jobs import JobRepository
from app.data.qdrant import QdrantVectorIndex
from app.services.models import (
    ChunkDraft,
    ClaimedJob,
    Element,
    EmbeddingModel,
    IndexTarget,
    Locator,
    VectorPoint,
)
from app.utils.errors import (
    ContextMeshError,
    IngestionFailure,
    LeaseLost,
    VectorIndexUnavailable,
)
from app.utils.logging import flow_event, flow_scope, job_flow
from app.utils.parsers import parse

PARSER_REVISION = "parser-v2"
EMBEDDING_BATCH = 64
logger = logging.getLogger("context_mesh.worker")


class JobHandler(Protocol):
    def handle(self, job: ClaimedJob) -> None: ...


class IngestionWorker:
    def __init__(self, jobs: JobRepository, handlers: Mapping[str, JobHandler], worker_id: str):
        self._jobs = jobs
        self._handlers = handlers
        self.worker_id = worker_id

    def run_once(self) -> bool:
        """Run one due job; False when the queue had nothing to do."""
        job = self._jobs.claim(self.worker_id)
        if job is None:
            return False
        flow = job_flow(job.kind)
        with flow_scope(job_id=job.id):
            flow_event(flow, "job_started", attempt=job.attempts)
            # `job_ended` follows every run that did not fail; a handler that found
            # nothing to do (deleted or replaced item) logs `job_cancelled` first.
            try:
                handler = self._handlers.get(job.kind)
                if handler is None:
                    raise IngestionFailure("unsupported_job")
                handler.handle(job)
            except LeaseLost:
                logger.warning("Lost job lease; job_id=%s", job.id)
                flow_event(flow, "lease_lost")
            except Exception as error:
                code, retryable = failure_details(error)
                logger.warning(
                    "Job failed; job_id=%s code=%s error=%s", job.id, code, type(error).__name__
                )
                flow_event(flow, "job_failed", code=code, retry=retryable)
                self._jobs.fail(job, code, retryable)
            else:
                flow_event(flow, "job_ended")
        return True


def failure_details(error: Exception) -> tuple[str, bool]:
    """The stored code and whether to retry; unknown errors are retried."""
    if isinstance(error, (IngestionFailure, ContextMeshError)):
        return error.code, error.retryable
    if isinstance(error, VectorIndexUnavailable):
        return "vector_index_unavailable", True
    return "internal_error", True


class DocumentIndexer:
    """Index one immutable document version and publish it only through fenced checks."""

    def __init__(
        self,
        jobs: JobRepository,
        store: IndexingRepository,
        blobs: FilesystemBlobStore,
        embeddings: EmbeddingModel,
        vectors: QdrantVectorIndex,
    ):
        self._jobs = jobs
        self._store = store
        self._blobs = blobs
        self._embeddings = embeddings
        self._vectors = vectors

    @property
    def signature(self) -> str:
        """Changes whenever parsing, chunking, or the embedding model changes."""
        return f"{PARSER_REVISION}|{CHUNKING.signature}|{self._embeddings.identity}"

    def handle(self, job: ClaimedJob) -> None:
        target = self._store.target(job)
        if target is None:
            self._jobs.cancel(job, "obsolete")
            flow_event(job_flow(job.kind), "job_cancelled", code="obsolete")
            return
        chunks = self._chunks(job, target)
        generation_id = self._store.stage(job, target, self.signature, chunks)
        for start in range(0, len(chunks), EMBEDDING_BATCH):
            self._jobs.heartbeat(job, "embedding")
            self._embed(target, generation_id, chunks[start : start + EMBEDDING_BATCH])
        self._jobs.heartbeat(job, "verifying")
        if self._vectors.count_generation(generation_id) != len(chunks):
            raise IngestionFailure("index_verification_failed", retryable=True)
        discarded = self._store.publish(job, target, generation_id)
        if discarded == generation_id:  # Deleted or replaced while indexing: not published.
            flow_event(job_flow(job.kind), "job_cancelled", code="obsolete")
        if discarded is not None:
            try:
                self._vectors.delete_generation(discarded)
            except VectorIndexUnavailable:
                logger.warning("Deferred vector cleanup; generation_id=%s", discarded)

    def _chunks(self, job: ClaimedJob, target: IndexTarget) -> tuple[ChunkDraft, ...]:
        self._jobs.heartbeat(job, "parsing")
        try:
            content = self._blobs.read(target.blob_key)
        except LookupError:
            raise IngestionFailure("content_unavailable") from None
        seed = f"{target.document_id}:{target.document_version_id}:{self.signature}"
        chunks = chunk_elements(parse(target.media_type, content), CHUNKING, seed)
        if not chunks:
            raise IngestionFailure("no_text")
        return chunks

    def _embed(self, target: IndexTarget, generation_id: UUID, batch: tuple[ChunkDraft, ...]):
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


class Eraser:
    """Physical cleanup after a deletion already hid the document or source."""

    def __init__(
        self,
        jobs: JobRepository,
        store: IndexingRepository,
        blobs: FilesystemBlobStore,
        vectors: QdrantVectorIndex,
    ):
        self._jobs = jobs
        self._store = store
        self._blobs = blobs
        self._vectors = vectors

    def handle(self, job: ClaimedJob) -> None:
        self._jobs.heartbeat(job, "erasing")
        if job.document_id is not None:
            self._vectors.delete_document(job.document_id)
        else:
            self._vectors.delete_source(job.source_id)
        for key in self._store.blob_keys(job):
            self._blobs.delete(key)
        self._jobs.succeed(job)


# Chunking: heading-aware, token-bounded, with deterministic chunk ids

TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]")
CHUNK_NAMESPACE = UUID("6f1d3c56-6a0e-4c71-9a43-8d2b7f1e0c55")


@dataclass(frozen=True)
class ChunkingPolicy:
    """Token estimates undercount unspaced scripts, so characters are also bounded."""

    max_tokens: int = 400
    overlap_tokens: int = 60
    max_chars: int = 2000
    max_chunks: int = 2000

    @property
    def signature(self) -> str:
        return f"chunker-v2:{self.max_tokens}:{self.overlap_tokens}:{self.max_chars}"

    def fits(self, tokens: int, characters: int) -> bool:
        return tokens <= self.max_tokens and characters <= self.max_chars


CHUNKING = ChunkingPolicy()


def estimate_tokens(text: str) -> int:
    """Approximate count of word and punctuation tokens; not a provider tokenizer."""
    return len(TOKEN_PATTERN.findall(text))


def chunk_elements(
    elements: tuple[Element, ...], policy: ChunkingPolicy, seed: str
) -> tuple[ChunkDraft, ...]:
    """Pack elements into chunks. A chunk never spans headings, pages, or slides, so each
    citation has one place; a new chunk in the same section starts with some overlap."""
    groups: list[list[Element]] = []
    for element in elements:
        for piece in _bounded(element, policy):
            current = groups[-1] if groups else None
            if current is None or _position(current[-1]) != _position(piece):
                groups.append([piece])
            elif _fits_group(current, piece, policy):
                current.append(piece)
            else:
                groups.append([*_overlap(current, piece, policy), piece])
    if len(groups) > policy.max_chunks:
        raise IngestionFailure("document_too_large")
    return tuple(_draft(group, ordinal, seed) for ordinal, group in enumerate(groups))


def _position(element: Element) -> tuple:
    return (element.heading_path, element.page, element.slide)


def _bounded(element: Element, policy: ChunkingPolicy) -> list[Element]:
    """An element too large for one chunk is split into word windows (very long words
    are sliced first)."""
    if policy.fits(estimate_tokens(element.text), len(element.text)):
        return [element]
    words = [
        word[start : start + policy.max_chars]
        for word in element.text.split()
        for start in range(0, len(word), policy.max_chars)
    ]
    windows: list[list[str]] = [[]]
    tokens = characters = 0
    for word in words:
        if windows[-1] and not policy.fits(tokens + estimate_tokens(word), characters + len(word)):
            windows.append([])
            tokens = characters = 0
        windows[-1].append(word)
        tokens += estimate_tokens(word)
        characters += len(word) + 1
    return [replace(element, text=" ".join(window)) for window in windows]


def _fits_group(group: list[Element], piece: Element, policy: ChunkingPolicy) -> bool:
    tokens = sum(estimate_tokens(element.text) for element in group) + estimate_tokens(piece.text)
    characters = sum(len(element.text) + 2 for element in group) + len(piece.text)
    return policy.fits(tokens, characters)


def _overlap(current: list[Element], piece: Element, policy: ChunkingPolicy) -> list[Element]:
    """The tail of the previous chunk that fits the overlap budget alongside `piece`."""
    budget = min(policy.overlap_tokens, policy.max_tokens - estimate_tokens(piece.text))
    carried: list[Element] = []
    for element in reversed(current):
        budget -= estimate_tokens(element.text)
        if budget < 0:
            break
        carried.insert(0, element)
    return carried if _fits_group(carried, piece, policy) else []


def _draft(group: list[Element], ordinal: int, seed: str) -> ChunkDraft:
    text = "\n\n".join(element.text for element in group)
    first = group[0]
    locator = Locator(
        first.heading_path,
        min(element.line_start for element in group),
        max(element.line_end for element in group),
        first.page,
        first.slide,
    )
    chunk_id = uuid5(CHUNK_NAMESPACE, f"{seed}:{ordinal}")
    return ChunkDraft(chunk_id, ordinal, text, locator, estimate_tokens(text))
