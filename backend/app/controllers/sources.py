"""Source, document, job, and evidence endpoints; uploads are bounded before parsing.
Storage keys are never exposed."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.controllers.http import Principal
from app.services.models import DocumentSummary, JobView, Passage, Source, UploadReceipt
from app.services.sources import SourceService
from app.services.validation import (
    invalid,
    normalized_description,
    normalized_source_name,
    too_large,
)

MULTIPART_OVERHEAD = 64_000


def source_service(request: Request) -> SourceService:
    return request.app.state.sources


Service = Annotated[SourceService, Depends(source_service)]


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


class LocatorResponse(BaseModel):
    """`page` is set for PDFs and `slide` for presentations; lines count within them."""

    model_config = ConfigDict(from_attributes=True)
    heading_path: list[str]
    line_start: int
    line_end: int
    page: int | None
    slide: int | None


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


router = APIRouter(prefix="/api/v1")


@router.get("/sources", response_model=SourceList)
def list_sources(service: Service, identity: Principal) -> dict[str, tuple[Source, ...]]:
    return {"items": service.sources(identity)}


@router.post("/sources", status_code=201, response_model=SourceResponse)
def create_source(body: CreateSource, service: Service, identity: Principal) -> Source:
    return service.create(identity, body.name, body.description, body.url)


@router.delete("/sources/{source_id}", status_code=202, response_model=AcceptedJob)
def delete_source(source_id: UUID, service: Service, identity: Principal) -> AcceptedJob:
    return AcceptedJob(job_id=service.delete_source(identity, source_id))


@router.post("/sources/{source_id}/sync", status_code=202, response_model=AcceptedJob)
def sync_source(source_id: UUID, service: Service, identity: Principal) -> AcceptedJob:
    return AcceptedJob(job_id=service.sync(identity, source_id))


@router.get("/sources/{source_id}/documents", response_model=DocumentList)
def list_documents(
    source_id: UUID, service: Service, identity: Principal
) -> dict[str, tuple[DocumentSummary, ...]]:
    return {"items": service.documents(identity, source_id)}


@router.post("/sources/{source_id}/documents", status_code=202, response_model=UploadResponse)
async def upload_document(
    source_id: UUID, request: Request, service: Service, identity: Principal
) -> UploadReceipt:
    """Bodies over the declared limit are refused before they are read."""
    limit = service.max_upload_bytes
    declared = request.headers.get("content-length", "")
    if not declared.isdigit():
        raise invalid("Uploads require a Content-Length header.")
    if int(declared) > limit + MULTIPART_OVERHEAD:
        raise too_large(limit)
    async with request.form(max_files=1, max_fields=1) as form:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise invalid("Attach one file in the 'file' form field.")
        data = await upload.read(limit + 1)
        filename = upload.filename or ""
    return await run_in_threadpool(service.upload, identity, source_id, filename, data)


@router.delete("/documents/{document_id}", status_code=202, response_model=AcceptedJob)
def delete_document(document_id: UUID, service: Service, identity: Principal) -> AcceptedJob:
    return AcceptedJob(job_id=service.delete_document(identity, document_id))


@router.get("/jobs/{job_id}", response_model=JobResponse)
def job_status(job_id: UUID, service: Service, identity: Principal) -> JobView:
    return service.job(identity, job_id)


@router.get(
    "/documents/{document_id}/versions/{version_id}/evidence", response_model=EvidenceResponse
)
def evidence(
    document_id: UUID,
    version_id: UUID,
    chunk_id: Annotated[UUID, Query()],
    service: Service,
    identity: Principal,
) -> Passage:
    return service.evidence(identity, document_id, version_id, chunk_id)
