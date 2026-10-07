"""Source, document, job, and passage values shared by ingestion and retrieval."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from app.domain.answers import Locator

SourceKind = Literal["upload"]
JobKind = Literal[
    "document.index_requested", "document.delete_requested", "source.delete_requested"
]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


@dataclass(frozen=True)
class Source:
    id: UUID
    kind: SourceKind
    name: str
    description: str
    document_count: int
    searchable_count: int
    created_at: datetime


@dataclass(frozen=True)
class CatalogSource:
    id: UUID
    name: str
    description: str
    searchable_count: int


@dataclass(frozen=True)
class JobView:
    id: UUID
    kind: JobKind
    status: JobStatus
    attempts: int
    stage: str | None
    error_code: str | None
    updated_at: datetime


@dataclass(frozen=True)
class DocumentSummary:
    id: UUID
    source_id: UUID
    title: str
    media_type: str
    searchable: bool
    chunk_count: int
    latest_job: JobView | None
    updated_at: datetime


@dataclass(frozen=True)
class UploadReceipt:
    document: DocumentSummary
    job_id: UUID
    duplicate: bool


@dataclass(frozen=True)
class ClaimedJob:
    id: UUID
    kind: str
    workspace_id: UUID
    source_id: UUID
    document_id: UUID | None
    document_version_id: UUID | None
    token: int
    attempts: int
    max_attempts: int


@dataclass(frozen=True)
class IndexTarget:
    workspace_id: UUID
    source_id: UUID
    document_id: UUID
    document_version_id: UUID
    blob_key: str
    media_type: str
    title: str


@dataclass(frozen=True)
class Element:
    text: str
    heading_path: tuple[str, ...]
    line_start: int
    line_end: int


@dataclass(frozen=True)
class ChunkDraft:
    id: UUID
    ordinal: int
    text: str
    locator: Locator
    token_count: int

    @property
    def embedding_text(self) -> str:
        if not self.locator.heading_path:
            return self.text
        return f"{' > '.join(self.locator.heading_path)}\n\n{self.text}"


@dataclass(frozen=True)
class Passage:
    chunk_id: UUID
    source_id: UUID
    document_id: UUID
    document_version_id: UUID
    index_generation_id: UUID
    source_name: str
    title: str
    locator: Locator
    text: str


@dataclass(frozen=True)
class Evidence:
    passage: Passage
    score: float
    round: int
