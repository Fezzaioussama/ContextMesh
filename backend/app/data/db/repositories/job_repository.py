"""PostgreSQL durable jobs: skip-locked claims, leases, fencing tokens, and retries."""

import random
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, RowMapping, and_, insert, or_, select, update
from sqlalchemy.sql.elements import ColumnElement

from app.data.db.models.knowledge import jobs
from app.services.rules.errors import LeaseLost
from app.services.rules.knowledge import ClaimedJob, JobView

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
    """Insert in the caller's transaction so the domain change and job commit together."""
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


def require_lease(connection: Connection, job: ClaimedJob) -> None:
    query = (
        select(jobs.c.id)
        .where(jobs.c.id == job.id, jobs.c.fencing_token == job.token, jobs.c.status == "running")
        .with_for_update()
    )
    if connection.execute(query).first() is None:
        raise LeaseLost()


def job_value(row: RowMapping) -> JobView:
    return JobView(
        row["id"],
        row["kind"],
        row["status"],
        row["attempts"],
        row["stage"],
        row["safe_error_code"],
        row["updated_at"],
    )


def finished(**values: object) -> dict[str, object]:
    return {"lease_owner": None, "lease_until": None, **values}


class JobRepository:
    def __init__(self, engine: Engine, lease_seconds: int, retry_seconds: float = 5.0):
        self._engine = engine
        self._lease = timedelta(seconds=lease_seconds)
        self._retry_seconds = retry_seconds

    def claim(self, worker_id: str) -> ClaimedJob | None:
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            self._exhaust(connection, now)
            row = connection.execute(self._due(now)).mappings().first()
            if row is None:
                return None
            return self._lease_row(connection, row, worker_id, now)

    def _exhaust(self, connection: Connection, now: datetime) -> None:
        connection.execute(
            update(jobs)
            .where(
                jobs.c.status == "running",
                jobs.c.lease_until < now,
                jobs.c.attempts >= jobs.c.max_attempts,
            )
            .values(**finished(status="failed", safe_error_code="attempts_exhausted"))
        )

    def _due(self, now: datetime):
        return (
            select(jobs)
            .where(_claimable(now))
            .order_by(jobs.c.next_attempt_at, jobs.c.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )

    def _lease_row(
        self, connection: Connection, row: RowMapping, worker_id: str, now: datetime
    ) -> ClaimedJob:
        token = row["fencing_token"] + 1
        attempts = row["attempts"] + 1
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
        if retryable and job.attempts < job.max_attempts:
            self._finish(job, self._retry(job, code))
            return
        self._finish(job, finished(status="failed", safe_error_code=code))

    def _retry(self, job: ClaimedJob, code: str) -> dict[str, object]:
        delay = self._retry_seconds * 2 ** (job.attempts - 1) * random.uniform(0.5, 1.5)
        due = datetime.now(UTC) + timedelta(seconds=delay)
        return finished(status="queued", safe_error_code=code, next_attempt_at=due)

    def _finish(self, job: ClaimedJob, values: dict[str, object]) -> None:
        """A replaced worker's late outcome is ignored; the current owner decides."""
        with self._engine.begin() as connection:
            fenced(connection, job, **values)


def _claimable(now: datetime) -> ColumnElement[bool]:
    queued = and_(jobs.c.status == "queued", jobs.c.next_attempt_at <= now)
    expired = and_(
        jobs.c.status == "running",
        jobs.c.lease_until < now,
        jobs.c.attempts < jobs.c.max_attempts,
    )
    return or_(queued, expired)
