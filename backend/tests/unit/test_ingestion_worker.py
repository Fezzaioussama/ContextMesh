"""Worker dispatch records a safe outcome for every failure class."""

from uuid import uuid4

import pytest
from app.services.ingestion.worker import IngestionWorker
from app.services.rules.errors import (
    IngestionFailure,
    LeaseLost,
    VectorIndexUnavailable,
    provider_unavailable,
)
from app.services.rules.knowledge import ClaimedJob


class Queue:
    def __init__(self, job):
        self.job = job
        self.failures = []

    def claim(self, worker_id):
        job, self.job = self.job, None
        return job

    def fail(self, job, code, retryable):
        self.failures.append((code, retryable))


class Raising:
    def __init__(self, error):
        self.error = error

    def handle(self, job):
        raise self.error


def job(kind="document.index_requested"):
    return ClaimedJob(uuid4(), kind, uuid4(), uuid4(), uuid4(), uuid4(), 1, 1, 5)


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (IngestionFailure("invalid_encoding"), ("invalid_encoding", False)),
        (IngestionFailure("index_verification_failed", True), ("index_verification_failed", True)),
        (provider_unavailable(), ("provider_unavailable", True)),
        (VectorIndexUnavailable(), ("vector_index_unavailable", True)),
        (RuntimeError("private detail"), ("internal_error", True)),
    ],
)
def test_failures_map_to_safe_codes_and_retry_policy(error, outcome):
    queue = Queue(job())
    worker = IngestionWorker(queue, {"document.index_requested": Raising(error)}, "worker")
    assert worker.run_once() is True
    assert queue.failures == [outcome]


def test_lost_lease_leaves_the_outcome_to_the_new_owner():
    queue = Queue(job())
    worker = IngestionWorker(queue, {"document.index_requested": Raising(LeaseLost())}, "worker")
    worker.run_once()
    assert queue.failures == []


def test_unknown_job_kind_fails_permanently():
    queue = Queue(job("unknown.kind"))
    IngestionWorker(queue, {}, "worker").run_once()
    assert queue.failures == [("unsupported_job", False)]


def test_idle_queue_reports_no_work():
    assert IngestionWorker(Queue(None), {}, "worker").run_once() is False
