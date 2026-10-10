"""Durable jobs in PostgreSQL: skip-locked claims, leases, fencing tokens, and retries.

A job is enqueued in the same transaction as the change that needs it. A worker claims
it with a fresh fencing token; every later write checks that token, so a worker whose
lease was taken over can no longer change anything.
"""

import random
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, RowMapping, and_, insert, or_, select, update

from app.data.db import jobs
from app.services.models import ClaimedJob, JobView
from app.utils.errors import LeaseLost

MAX_ATTEMPTS = 5


def enqueue(
    connection: Connection,
    *,
    workspace_id: UUID,
    kind: str,
    source_id: UUID,
    document_id: UUID | None = None,
    version_id: UUID | None = None,
) -> UUID:
    """Insert in the caller's transaction so the change and its job commit together."""
    job_id = uuid4()
    now = datetime.now(UTC)
    connection.execute(
        insert(jobs).values(
            id=job_id,
            workspace_id=workspace_id,
            kind=kind,
            source_id=source_id,
            document_id=document_id,
            document_version_id=version_id,
            status="queued",
            attempts=0,
            max_attempts=MAX_ATTEMPTS,
            fencing_token=0,
            next_attempt_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    return job_id


def fenced(connection: Connection, job: ClaimedJob, **values: object) -> bool:
    """Apply a change only while this worker's token still owns the running job."""
    result = connection.execute(
        update(jobs)
        .where(jobs.c.id == job.id, jobs.c.fencing_token == job.token, jobs.c.status == "running")
        .values(updated_at=datetime.now(UTC), **values)
    )
    return result.rowcount == 1


def finished(**values: object) -> dict[str, object]:
    return {"lease_owner": None, "lease_until": None, **values}


def require_lease(connection: Connection, job: ClaimedJob) -> None:
    """Lock the job row for this transaction, or stop if another worker owns it now."""
    query = (
        select(jobs.c.id)
        .where(jobs.c.id == job.id, jobs.c.fencing_token == job.token, jobs.c.status == "running")
        .with_for_update()
    )
    if connection.execute(query).first() is None:
        raise LeaseLost()


def job_view(row: RowMapping) -> JobView:
    return JobView(
        row["id"],
        row["kind"],
        row["status"],
        row["attempts"],
        row["stage"],
        row["safe_error_code"],
        row["updated_at"],
    )


class JobRepository:
    def __init__(self, engine: Engine, lease_seconds: int, retry_seconds: float = 5.0):
        self._engine = engine
        self._lease = timedelta(seconds=lease_seconds)
        self._retry_seconds = retry_seconds

    def claim(self, worker_id: str) -> ClaimedJob | None:
        """The next due job: queued, or running with an expired lease and attempts left."""
        now = datetime.now(UTC)
        expired = and_(jobs.c.status == "running", jobs.c.lease_until < now)
        due = (
            select(jobs)
            .where(
                or_(
                    and_(jobs.c.status == "queued", jobs.c.next_attempt_at <= now),
                    and_(expired, jobs.c.attempts < jobs.c.max_attempts),
                )
            )
            .order_by(jobs.c.next_attempt_at, jobs.c.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        with self._engine.begin() as connection:
            connection.execute(
                update(jobs)
                .where(expired, jobs.c.attempts >= jobs.c.max_attempts)
                .values(**finished(status="failed", safe_error_code="attempts_exhausted"))
            )
            row = connection.execute(due).mappings().first()
            if row is None:
                return None
            token, attempts = row["fencing_token"] + 1, row["attempts"] + 1
            connection.execute(
                update(jobs)
                .where(jobs.c.id == row["id"])
                .values(
                    status="running",
                    attempts=attempts,
                    fencing_token=token,
                    lease_owner=worker_id,
                    lease_until=now + self._lease,
                    stage="claimed",
                    updated_at=now,
                )
            )
        return ClaimedJob(
            row["id"],
            row["kind"],
            row["workspace_id"],
            row["source_id"],
            row["document_id"],
            row["document_version_id"],
            token,
            attempts,
            row["max_attempts"],
        )

    def heartbeat(self, job: ClaimedJob, stage: str) -> None:
        lease_until = datetime.now(UTC) + self._lease
        with self._engine.begin() as connection:
            if not fenced(connection, job, stage=stage, lease_until=lease_until):
                raise LeaseLost()

    def succeed(self, job: ClaimedJob) -> None:
        self._finish(job, finished(status="succeeded", stage="done"))

    def cancel(self, job: ClaimedJob, code: str) -> None:
        self._finish(job, finished(status="cancelled", safe_error_code=code))

    def fail(self, job: ClaimedJob, code: str, retryable: bool) -> None:
        """Retryable failures go back to the queue with jittered exponential backoff."""
        if retryable and job.attempts < job.max_attempts:
            delay = self._retry_seconds * 2 ** (job.attempts - 1) * random.uniform(0.5, 1.5)
            due = datetime.now(UTC) + timedelta(seconds=delay)
            self._finish(job, finished(status="queued", safe_error_code=code, next_attempt_at=due))
            return
        self._finish(job, finished(status="failed", safe_error_code=code))

    def _finish(self, job: ClaimedJob, values: dict[str, object]) -> None:
        """A replaced worker's late outcome is ignored; the current owner decides."""
        with self._engine.begin() as connection:
            fenced(connection, job, **values)
