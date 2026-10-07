"""Physical cleanup after canonical tombstones have already removed visibility."""

from app.domain.knowledge import ClaimedJob
from app.services.ports.ingestion import IndexingStore, JobQueue, VectorWriter
from app.services.ports.sources import BlobStore


class _Eraser:
    def __init__(
        self, jobs: JobQueue, store: IndexingStore, blobs: BlobStore, vectors: VectorWriter
    ):
        self._jobs = jobs
        self._store = store
        self._blobs = blobs
        self._vectors = vectors

    def _delete_blobs(self, job: ClaimedJob) -> None:
        for key in self._store.blob_keys(job):
            self._blobs.delete(key)
        self._jobs.succeed(job)


class DocumentEraser(_Eraser):
    def handle(self, job: ClaimedJob) -> None:
        self._jobs.heartbeat(job, "erasing")
        if job.document_id is not None:
            self._vectors.delete_document(job.document_id)
        self._delete_blobs(job)


class SourceEraser(_Eraser):
    def handle(self, job: ClaimedJob) -> None:
        self._jobs.heartbeat(job, "erasing")
        self._vectors.delete_source(job.source_id)
        self._delete_blobs(job)
