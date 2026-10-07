"""Source, document, job, and evidence HTTP shapes; storage keys are never exposed."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from app.controllers.schemas.answers import LocatorResponse
from app.services.rules.uploads import normalized_description, normalized_source_name


class CreateSource(BaseModel):
    """Omit `url` for an upload source; a `url` creates a website source to crawl."""

    model_config = ConfigDict(extra="forbid")
    name: StrictStr = Field(min_length=1, max_length=100)
    description: StrictStr = Field(default="", max_length=500)
    url: StrictStr | None = Field(default=None, min_length=1, max_length=2000)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        return normalized_source_name(value)

    @field_validator("description")
    @classmethod
    def valid_description(cls, value: str) -> str:
        return normalized_description(value)


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    kind: str
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    attempts: int
    stage: str | None
    error_code: str | None
    updated_at: datetime


class SourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    kind: Literal["upload", "website"]
    name: str
    description: str
    url: str | None
    document_count: int
    searchable_count: int
    latest_sync: JobResponse | None
    created_at: datetime


class SourceList(BaseModel):
    items: list[SourceResponse]


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    source_id: UUID
    title: str
    media_type: str
    searchable: bool
    chunk_count: int
    latest_job: JobResponse | None
    uri: str | None
    updated_at: datetime


class DocumentList(BaseModel):
    items: list[DocumentResponse]


class UploadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    document: DocumentResponse
    job_id: UUID
    duplicate: bool


class AcceptedJob(BaseModel):
    job_id: UUID


class EvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    chunk_id: UUID
    source_id: UUID
    document_id: UUID
    document_version_id: UUID
    index_generation_id: UUID
    source_name: str
    title: str
    locator: LocatorResponse
    text: str
    uri: str | None
