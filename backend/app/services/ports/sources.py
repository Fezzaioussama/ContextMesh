"""Ports for scoped source management and opaque raw-content storage."""

from typing import Protocol
from uuid import UUID

from app.services.rules.knowledge import (
    DocumentSummary,
    JobView,
    Passage,
    Source,
    SourceDraft,
    UploadReceipt,
)
from app.services.rules.uploads import UploadSpec
from app.utils.security import Identity


class SourceStore(Protocol):
    def create(self, identity: Identity, draft: SourceDraft) -> Source: ...

    def sources(self, identity: Identity) -> tuple[Source, ...]: ...

    def documents(self, identity: Identity, source_id: UUID) -> tuple[DocumentSummary, ...]: ...

    def register_upload(
        self, identity: Identity, source_id: UUID, spec: UploadSpec, digest: str, blob_key: str
    ) -> UploadReceipt: ...

    def request_sync(self, identity: Identity, source_id: UUID) -> UUID: ...

    def delete_document(self, identity: Identity, document_id: UUID) -> UUID: ...

    def delete_source(self, identity: Identity, source_id: UUID) -> UUID: ...

    def job(self, identity: Identity, job_id: UUID) -> JobView: ...


class EvidenceReader(Protocol):
    def passage(
        self, identity: Identity, document_id: UUID, version_id: UUID, chunk_id: UUID
    ) -> Passage: ...


class BlobStore(Protocol):
    def write(self, data: bytes) -> str: ...

    def read(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...
