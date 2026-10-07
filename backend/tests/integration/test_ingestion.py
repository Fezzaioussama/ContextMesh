"""Durable ingestion: idempotent versions, fenced publication, failures, and recovery."""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.data.db.models.knowledge import documents, index_generations, jobs, sources
from app.data.db.repositories.indexing_repository import IndexingRepository
from app.data.db.repositories.job_repository import JobRepository
from app.data.db.repositories.retrieval_repository import RetrievalRepository
from app.services.rules.answers import Locator
from app.services.rules.errors import LeaseLost, provider_unavailable
from app.services.rules.knowledge import ChunkDraft
from app.utils.exceptions import ContextMeshError
from sqlalchemy import select, update
from support import create_source, drain, upload
from support import documents as listed_documents

pytestmark = pytest.mark.integration
V1 = "# Policy\n\nVersion one says tokens expire hourly."
V2 = "# Policy\n\nVersion two says tokens expire daily."


def generations(engine, document_id):
    query = (
        select(index_generations.c.id, index_generations.c.state)
        .where(index_generations.c.document_id == UUID(document_id))
        .order_by(index_generations.c.created_at)
    )
    with engine.begin() as connection:
        return [tuple(row) for row in connection.execute(query)]


def active_generation(engine, document_id):
    query = select(documents.c.active_generation_id).where(documents.c.id == document_id)
    with engine.begin() as connection:
        return connection.execute(query).scalar()


def job_row(engine, job_id):
    with engine.begin() as connection:
        return connection.execute(select(jobs).where(jobs.c.id == UUID(job_id))).mappings().one()


def make_due(engine):
    with engine.begin() as connection:
        connection.execute(
            update(jobs)
            .where(jobs.c.status == "queued")
            .values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
        )


def passages(client, source_id, text):
    conversation = client.post("/api/v1/assistant/conversations", json={}).json()["id"]
    body = {"message": text, "source_ids": [source_id]}
    response = client.post(
        f"/api/v1/assistant/conversations/{conversation}/messages",
        json=body,
        headers={"Idempotency-Key": text},
    )
    return [item["snippet"] for item in response.json()["assistant_message"]["answer"]["citations"]]


def test_identical_reupload_is_idempotent_and_keeps_one_blob(client, worker, tmp_path):
    source_id = create_source(client)
    first = upload(client, source_id, "policy.md", V1).json()
    drain(worker)
    again = upload(client, source_id, "policy.md", V1).json()
    assert again["duplicate"] is True
    assert again["job_id"] == first["job_id"]
    assert len([path for path in tmp_path.rglob("*") if path.is_file()]) == 1


def test_new_version_replaces_the_published_generation(client, worker, engine):
    source_id = create_source(client)
    document_id = upload(client, source_id, "policy.md", V1).json()["document"]["id"]
    drain(worker)
    upload(client, source_id, "policy.md", V2)
    drain(worker)
    states = [state for _, state in generations(engine, document_id)]
    assert states == ["superseded", "published"]
    assert passages(client, source_id, "tokens expire") == ["Version two says tokens expire daily."]


def test_failed_first_index_stays_unavailable_then_recovers(client, worker, engine, embeddings):
    source_id = create_source(client)
    receipt = upload(client, source_id, "policy.md", V1).json()
    embeddings.failure = provider_unavailable()
    drain(worker)
    row = job_row(engine, receipt["job_id"])
    embeddings.failure = None
    pending = (
        row["status"],
        row["safe_error_code"],
        row["attempts"],
        listed_documents(client, source_id)[0]["searchable"],
        passages(client, source_id, "tokens expire"),
    )
    assert pending == ("queued", "provider_unavailable", 1, False, [])
    make_due(engine)
    drain(worker)
    assert listed_documents(client, source_id)[0]["searchable"] is True


def test_failed_reindex_preserves_the_previous_publication(client, worker, engine, embeddings):
    source_id = create_source(client)
    upload(client, source_id, "policy.md", V1)
    drain(worker)
    upload(client, source_id, "policy.md", V2)
    embeddings.failure = provider_unavailable()
    drain(worker)
    embeddings.failure = None
    assert passages(client, source_id, "tokens expire") == [
        "Version one says tokens expire hourly."
    ]


def test_undecodable_upload_fails_permanently_with_a_safe_code(client, worker, engine):
    source_id = create_source(client)
    files = {"file": ("broken.txt", b"\xff\xfe\x00 not utf8", "text/plain")}
    receipt = client.post(f"/api/v1/sources/{source_id}/documents", files=files).json()
    drain(worker)
    row = job_row(engine, receipt["job_id"])
    assert (row["status"], row["safe_error_code"]) == ("failed", "invalid_encoding")
    assert listed_documents(client, source_id)[0]["searchable"] is False


def test_older_version_cannot_replace_a_newer_one(client, worker, engine):
    source_id = create_source(client)
    first = upload(client, source_id, "policy.md", V1).json()
    second = upload(client, source_id, "policy.md", V2).json()
    with engine.begin() as connection:
        connection.execute(
            update(jobs)
            .where(jobs.c.id == UUID(first["job_id"]))
            .values(next_attempt_at=datetime.now(UTC) + timedelta(hours=1))
        )
    drain(worker)
    make_due(engine)
    drain(worker)
    assert job_row(engine, first["job_id"])["safe_error_code"] == "obsolete"
    assert job_row(engine, second["job_id"])["status"] == "succeeded"
    assert passages(client, source_id, "tokens expire") == ["Version two says tokens expire daily."]


def test_source_deleted_during_indexing_is_never_published(client, engine, services):
    source_id = create_source(client)
    upload(client, source_id, "policy.md", V1)
    queue = JobRepository(engine, 90)
    store = IndexingRepository(engine)
    job = queue.claim("worker-a")
    target = store.target(job)
    deleted = client.delete(f"/api/v1/sources/{source_id}").status_code
    generation = store.stage(job, target, "signature", ())
    discarded = store.publish(job, target, generation)
    outcome = (deleted, discarded, active_generation(engine, target.document_id))
    assert outcome == (202, generation, None)
    assert job_row(engine, str(job.id))["status"] == "cancelled"


def claimed_target(client, engine):
    upload(client, create_source(client), "policy.md", V1)
    job = JobRepository(engine, 90).claim("worker-a")
    store = IndexingRepository(engine)
    return job, store, store.target(job)


def test_publication_waits_for_a_concurrent_source_deletion(client, engine):
    job, store, target = claimed_target(client, engine)
    generation = store.stage(job, target, "signature", ())
    with engine.connect() as deleting, ThreadPoolExecutor(max_workers=1) as pool:
        transaction = deleting.begin()
        deleting.execute(
            update(sources)
            .where(sources.c.id == target.source_id)
            .values(deleted_at=datetime.now(UTC))
        )
        publishing = pool.submit(store.publish, job, target, generation)
        time.sleep(0.5)
        blocked = not publishing.done()
        transaction.commit()
        discarded = publishing.result(timeout=10)
    assert (blocked, discarded, active_generation(engine, target.document_id)) == (
        True,
        generation,
        None,
    )


def test_unpublished_chunks_are_not_served_as_evidence(client, engine, identity):
    job, store, target = claimed_target(client, engine)
    chunk = ChunkDraft(uuid4(), 0, "Staged only.", Locator((), 1, 1), 2)
    store.stage(job, target, "signature", (chunk,))
    with pytest.raises(ContextMeshError) as failure:
        RetrievalRepository(engine).passage(
            identity, target.document_id, target.document_version_id, chunk.id
        )
    assert failure.value.code == "not_found"


def test_replaced_worker_cannot_heartbeat_or_publish(client, engine):
    source_id = create_source(client)
    upload(client, source_id, "policy.md", V1)
    queue = JobRepository(engine, 90)
    store = IndexingRepository(engine)
    stale = queue.claim("worker-a")
    target = store.target(stale)
    generation = store.stage(stale, target, "signature", ())
    with engine.begin() as connection:
        connection.execute(
            update(jobs)
            .where(jobs.c.id == stale.id)
            .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
        )
    current = queue.claim("worker-b")
    assert (current.id, current.token) == (stale.id, stale.token + 1)
    with pytest.raises(LeaseLost):
        queue.heartbeat(stale, "embedding")
    with pytest.raises(LeaseLost):
        store.publish(stale, target, generation)
    queue.succeed(stale)
    assert job_row(engine, str(stale.id))["status"] == "running"


def test_two_workers_never_hold_the_same_live_lease(client, engine):
    source_id = create_source(client)
    upload(client, source_id, "policy.md", V1)
    queue = JobRepository(engine, 90)
    first = queue.claim("worker-a")
    assert first is not None
    assert queue.claim("worker-b") is None


def test_expired_job_out_of_attempts_fails_terminally(client, engine):
    source_id = create_source(client)
    receipt = upload(client, source_id, "policy.md", V1).json()
    queue = JobRepository(engine, 90)
    queue.claim("worker-a")
    with engine.begin() as connection:
        connection.execute(
            update(jobs)
            .where(jobs.c.id == UUID(receipt["job_id"]))
            .values(attempts=5, lease_until=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert queue.claim("worker-b") is None
    row = job_row(engine, receipt["job_id"])
    assert (row["status"], row["safe_error_code"]) == ("failed", "attempts_exhausted")
