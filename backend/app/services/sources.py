"""Source management use cases; indexing itself happens later in the worker."""

from hashlib import sha256
from uuid import UUID

from app.core.security import Identity
from app.domain.knowledge import DocumentSummary, JobView, Passage, Source, UploadReceipt
from app.domain.uploads import checked_upload, normalized_description, normalized_source_name
from app.services.ports.sources import BlobStore, EvidenceReader, SourceStore


class SourceService:
    def __init__(
        self,
        store: SourceStore,
        evidence: EvidenceReader,
        blobs: BlobStore,
        *,
        max_upload_bytes: int,
    ):
        self._store = store
        self._evidence = evidence
        self._blobs = blobs
        self.max_upload_bytes = max_upload_bytes

    def create(self, identity: Identity, name: str, description: str) -> Source:
        return self._store.create(
            identity, normalized_source_name(name), normalized_description(description)
        )

    def sources(self, identity: Identity) -> tuple[Source, ...]:
        return self._store.sources(identity)

    def documents(self, identity: Identity, source_id: UUID) -> tuple[DocumentSummary, ...]:
        return self._store.documents(identity, source_id)

    def upload(
        self, identity: Identity, source_id: UUID, filename: str, data: bytes
    ) -> UploadReceipt:
        spec = checked_upload(filename, len(data), self.max_upload_bytes)
        key = self._blobs.write(data)
        try:
            receipt = self._store.register_upload(
                identity, source_id, spec, sha256(data).hexdigest(), key
            )
        except BaseException:
            self._blobs.delete(key)
            raise
        if receipt.duplicate:
            self._blobs.delete(key)
        return receipt

    def delete_document(self, identity: Identity, document_id: UUID) -> UUID:
        return self._store.delete_document(identity, document_id)

    def delete_source(self, identity: Identity, source_id: UUID) -> UUID:
        return self._store.delete_source(identity, source_id)

    def job(self, identity: Identity, job_id: UUID) -> JobView:
        return self._store.job(identity, job_id)

    def evidence(
        self, identity: Identity, document_id: UUID, version_id: UUID, chunk_id: UUID
    ) -> Passage:
        return self._evidence.passage(identity, document_id, version_id, chunk_id)
