"""Source management: create sources, upload files, request crawls, delete, and show
cited evidence. Indexing itself happens later, in the worker."""

from hashlib import sha256
from uuid import UUID

from app.data.blobs import FilesystemBlobStore
from app.data.retrieval import RetrievalRepository
from app.data.sources import SourceRepository
from app.services.models import DocumentSummary, Identity, JobView, Passage, Source, UploadReceipt
from app.services.urls import checked_start_url
from app.services.validation import checked_upload, normalized_description, normalized_source_name
from app.utils.logging import flow_event


class SourceService:
    def __init__(
        self,
        store: SourceRepository,
        evidence: RetrievalRepository,
        blobs: FilesystemBlobStore,
        *,
        max_upload_bytes: int,
    ):
        self._store = store
        self._evidence = evidence
        self._blobs = blobs
        self.max_upload_bytes = max_upload_bytes

    def create(
        self, identity: Identity, name: str, description: str, url: str | None = None
    ) -> Source:
        """A URL makes a website source that the worker crawls; otherwise files are uploaded."""
        kind = "upload" if url is None else "website"
        source = self._store.create(
            identity,
            kind,
            normalized_source_name(name),
            normalized_description(description),
            None if url is None else checked_start_url(url),
        )
        flow_event("add_source", "created", source_id=source.id, kind=source.kind)
        return source

    def sync(self, identity: Identity, source_id: UUID) -> UUID:
        job_id = self._store.request_sync(identity, source_id)
        flow_event("crawl_website", "requested", source_id=source_id, job_id=job_id)
        return job_id

    def sources(self, identity: Identity) -> tuple[Source, ...]:
        return self._store.sources(identity)

    def documents(self, identity: Identity, source_id: UUID) -> tuple[DocumentSummary, ...]:
        return self._store.documents(identity, source_id)

    def upload(
        self, identity: Identity, source_id: UUID, filename: str, data: bytes
    ) -> UploadReceipt:
        """Store the bytes, then record the version and its index job in one transaction.
        Identical content is a duplicate and keeps no extra blob."""
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
        flow_event(
            "upload_document",
            "accepted",
            document_id=receipt.document.id,
            job_id=receipt.job_id,
            duplicate=receipt.duplicate,
        )
        return receipt

    def delete_document(self, identity: Identity, document_id: UUID) -> UUID:
        job_id = self._store.delete_document(identity, document_id)
        flow_event("delete_document", "requested", document_id=document_id, job_id=job_id)
        return job_id

    def delete_source(self, identity: Identity, source_id: UUID) -> UUID:
        job_id = self._store.delete_source(identity, source_id)
        flow_event("delete_source", "requested", source_id=source_id, job_id=job_id)
        return job_id

    def job(self, identity: Identity, job_id: UUID) -> JobView:
        return self._store.job(identity, job_id)

    def evidence(
        self, identity: Identity, document_id: UUID, version_id: UUID, chunk_id: UUID
    ) -> Passage:
        return self._evidence.passage(identity, document_id, version_id, chunk_id)
