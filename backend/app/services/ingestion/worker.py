"""Claim one durable job at a time and record a safe outcome for every failure."""

import logging
from collections.abc import Mapping
from typing import Protocol

from app.core.exceptions import ContextMeshError
from app.domain.errors import IngestionFailure, LeaseLost, VectorIndexUnavailable
from app.domain.knowledge import ClaimedJob
from app.services.ports.ingestion import JobQueue

logger = logging.getLogger("context_mesh.worker")


class JobHandler(Protocol):
    def handle(self, job: ClaimedJob) -> None: ...


def failure_details(error: Exception) -> tuple[str, bool]:
    if isinstance(error, IngestionFailure):
        return error.code, error.retryable
    if isinstance(error, ContextMeshError):
        return error.code, error.retryable
    if isinstance(error, VectorIndexUnavailable):
        return "vector_index_unavailable", True
    return "internal_error", True


class IngestionWorker:
    def __init__(self, jobs: JobQueue, handlers: Mapping[str, JobHandler], worker_id: str):
        self._jobs = jobs
        self._handlers = handlers
        self.worker_id = worker_id

    def run_once(self) -> bool:
        job = self._jobs.claim(self.worker_id)
        if job is None:
            return False
        self._execute(job)
        return True

    def _execute(self, job: ClaimedJob) -> None:
        try:
            self._handler(job).handle(job)
        except LeaseLost:
            logger.warning("Lost job lease; job_id=%s", job.id)
        except Exception as error:
            self._record(job, error)

    def _handler(self, job: ClaimedJob) -> JobHandler:
        handler = self._handlers.get(job.kind)
        if handler is None:
            raise IngestionFailure("unsupported_job")
        return handler

    def _record(self, job: ClaimedJob, error: Exception) -> None:
        code, retryable = failure_details(error)
        logger.warning("Job failed; job_id=%s code=%s error=%s", job.id, code, type(error).__name__)
        self._jobs.fail(job, code, retryable)
