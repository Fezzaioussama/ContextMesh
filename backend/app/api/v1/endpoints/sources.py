"""Source, document, job, and evidence endpoints; uploads are bounded before parsing."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.api.deps import principal, source_service
from app.core.exceptions import invalid_input
from app.core.security import Identity
from app.domain.errors import payload_too_large
from app.domain.knowledge import DocumentSummary, JobView, Passage, Source, UploadReceipt
from app.schemas.sources import (
    AcceptedJob,
    CreateSource,
    DocumentList,
    EvidenceResponse,
    JobResponse,
    SourceList,
    SourceResponse,
    UploadResponse,
)
from app.services.sources import SourceService

MULTIPART_OVERHEAD = 64_000
Service = Annotated[SourceService, Depends(source_service)]
Principal = Annotated[Identity, Depends(principal)]


def list_sources(service: Service, identity: Principal) -> dict[str, tuple[Source, ...]]:
    return {"items": service.sources(identity)}


def create_source(body: CreateSource, service: Service, identity: Principal) -> Source:
    return service.create(identity, body.name, body.description, body.url)


def sync_source(source_id: UUID, service: Service, identity: Principal) -> AcceptedJob:
    return AcceptedJob(job_id=service.sync(identity, source_id))


def delete_source(source_id: UUID, service: Service, identity: Principal) -> AcceptedJob:
    return AcceptedJob(job_id=service.delete_source(identity, source_id))


def list_documents(
    source_id: UUID, service: Service, identity: Principal
) -> dict[str, tuple[DocumentSummary, ...]]:
    return {"items": service.documents(identity, source_id)}


async def upload_document(
    source_id: UUID, request: Request, service: Service, identity: Principal
) -> UploadReceipt:
    limit = service.max_upload_bytes
    _require_declared_size(request, limit)
    async with request.form(max_files=1, max_fields=1) as form:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise invalid_input("Attach one file in the 'file' form field.")
        data = await upload.read(limit + 1)
        filename = upload.filename or ""
    return await run_in_threadpool(service.upload, identity, source_id, filename, data)


def _require_declared_size(request: Request, limit: int) -> None:
    declared = request.headers.get("content-length", "")
    if not declared.isdigit():
        raise invalid_input("Uploads require a Content-Length header.")
    if int(declared) > limit + MULTIPART_OVERHEAD:
        raise payload_too_large(limit)


def delete_document(document_id: UUID, service: Service, identity: Principal) -> AcceptedJob:
    return AcceptedJob(job_id=service.delete_document(identity, document_id))


def job_status(job_id: UUID, service: Service, identity: Principal) -> JobView:
    return service.job(identity, job_id)


def evidence(
    document_id: UUID,
    version_id: UUID,
    chunk_id: Annotated[UUID, Query()],
    service: Service,
    identity: Principal,
) -> Passage:
    return service.evidence(identity, document_id, version_id, chunk_id)


def knowledge_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    router.add_api_route("/sources", list_sources, response_model=SourceList)
    router.add_api_route(
        "/sources", create_source, methods=["POST"], status_code=201, response_model=SourceResponse
    )
    router.add_api_route(
        "/sources/{source_id}",
        delete_source,
        methods=["DELETE"],
        status_code=202,
        response_model=AcceptedJob,
    )
    router.add_api_route(
        "/sources/{source_id}/sync",
        sync_source,
        methods=["POST"],
        status_code=202,
        response_model=AcceptedJob,
    )
    router.add_api_route(
        "/sources/{source_id}/documents", list_documents, response_model=DocumentList
    )
    router.add_api_route(
        "/sources/{source_id}/documents",
        upload_document,
        methods=["POST"],
        status_code=202,
        response_model=UploadResponse,
    )
    router.add_api_route(
        "/documents/{document_id}",
        delete_document,
        methods=["DELETE"],
        status_code=202,
        response_model=AcceptedJob,
    )
    router.add_api_route("/jobs/{job_id}", job_status, response_model=JobResponse)
    router.add_api_route(
        "/documents/{document_id}/versions/{version_id}/evidence",
        evidence,
        response_model=EvidenceResponse,
    )
    return router
