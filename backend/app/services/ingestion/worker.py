"""Claim one durable job at a time and record a safe outcome for every failure."""

import logging
from collections.abc import Mapping
from typing import Protocol

from app.services.ports.ingestion import JobQueue
from app.services.rules.errors import IngestionFailure, LeaseLost, VectorIndexUnavailable
from app.services.rules.knowledge import ClaimedJob
from app.utils.exceptions import ContextMeshError
from app.utils.logging import flow_event, job_flow

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
        flow_event(job_flow(job.kind), "job_started", job_id=job.id, attempt=job.attempts)
        try:
            self._handler(job).handle(job)
        except LeaseLost:
            logger.warning("Lost job lease; job_id=%s", job.id)
        except Exception as error:
            self._record(job, error)
        else:
            flow_event(job_flow(job.kind), "job_finished", job_id=job.id)

    def _handler(self, job: ClaimedJob) -> JobHandler:
        handler = self._handlers.get(job.kind)
        if handler is None:
            raise IngestionFailure("unsupported_job")
        return handler

    def _record(self, job: ClaimedJob, error: Exception) -> None:
        code, retryable = failure_details(error)
        logger.warning("Job failed; job_id=%s code=%s error=%s", job.id, code, type(error).__name__)
        flow_event(job_flow(job.kind), "job_failed", job_id=job.id, code=code, retry=retryable)
        self._jobs.fail(job, code, retryable)
