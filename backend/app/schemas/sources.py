"""Source, document, job, and evidence HTTP shapes; storage keys are never exposed."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from app.domain.uploads import normalized_description, normalized_source_name
from app.schemas.answers import LocatorResponse


class CreateSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: StrictStr = Field(min_length=1, max_length=100)
    description: StrictStr = Field(default="", max_length=500)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        return normalized_source_name(value)

    @field_validator("description")
    @classmethod
    def valid_description(cls, value: str) -> str:
        return normalized_description(value)


class SourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    kind: Literal["upload"]
    name: str
    description: str
    document_count: int
    searchable_count: int
    created_at: datetime


class SourceList(BaseModel):
    items: list[SourceResponse]


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    kind: str
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    attempts: int
    stage: str | None
    error_code: str | None
    updated_at: datetime


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    source_id: UUID
    title: str
    media_type: str
    searchable: bool
    chunk_count: int
    latest_job: JobResponse | None
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
