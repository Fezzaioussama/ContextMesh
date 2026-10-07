"""Integration isolation: the durable job queue is shared by every test workspace."""

import pytest
from app.data.db.models.knowledge import jobs
from sqlalchemy import update


def _cancel_open_jobs(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            update(jobs)
            .where(jobs.c.status.in_(("queued", "running")))
            .values(status="cancelled", safe_error_code="test_isolation")
        )


@pytest.fixture(autouse=True)
def isolated_job_queue(engine):
    """Jobs from earlier tests use other blob directories and must not be claimed here."""
    _cancel_open_jobs(engine)
    yield
    _cancel_open_jobs(engine)
