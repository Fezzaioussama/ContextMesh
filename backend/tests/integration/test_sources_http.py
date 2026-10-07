"""Source and document HTTP contract: bounded uploads, roles, isolation, deletion."""

from uuid import UUID, uuid4

import pytest
from app.bootstrap.api import create_app
from app.core.config import Settings
from app.core.security import Identity
from app.db.models.user import memberships
from fastapi.testclient import TestClient
from sqlalchemy import update
from support import ask, create_source, documents, drain, upload

pytestmark = pytest.mark.integration


def test_sources_list_counts_documents_and_searchability(client, worker):
    source_id = create_source(client, "Handbook", "Platform handbook")
    upload(client, source_id, "one.md", "# One\n\nFirst.")
    queued = client.get("/api/v1/sources").json()["items"]
    drain(worker)
    ready = client.get("/api/v1/sources").json()["items"]
    assert [(item["document_count"], item["searchable_count"]) for item in queued] == [(1, 0)]
    assert [(item["document_count"], item["searchable_count"]) for item in ready] == [(1, 1)]
    assert ready[0]["description"] == "Platform handbook"


def test_document_listing_reports_job_progress_and_chunk_counts(client, worker):
    source_id = create_source(client)
    receipt = upload(client, source_id, "guide.md", "# Guide\n\nAlpha.\n\n## Part\n\nBeta.").json()
    assert (receipt["duplicate"], receipt["document"]["latest_job"]["status"]) == (False, "queued")
    drain(worker)
    [listed] = documents(client, source_id)
    job = client.get(f"/api/v1/jobs/{receipt['job_id']}").json()
    latest = listed["latest_job"]
    assert (listed["searchable"], listed["chunk_count"], latest["error_code"]) == (True, 2, None)
    assert (latest["status"], job["status"], job["attempts"]) == ("succeeded", "succeeded", 1)


@pytest.mark.parametrize(
    ("filename", "content", "status", "code"),
    [
        ("report.pdf", b"%PDF", 415, "unsupported_media_type"),
        ("empty.md", b"", 422, "invalid_input"),
        ("large.md", b"x" * 2048, 413, "payload_too_large"),
    ],
)
def test_upload_rejections_are_specific_and_store_nothing(
    database_url, identity, services, tmp_path, filename, content, status, code
):
    settings = Settings(
        _env_file=None, database_url=database_url, blob_dir=tmp_path, max_upload_bytes=1024
    )
    with TestClient(create_app(settings, services, identity)) as limited:
        source_id = create_source(limited)
        files = {"file": (filename, content, "application/octet-stream")}
        response = limited.post(f"/api/v1/sources/{source_id}/documents", files=files)
        listed = documents(limited, source_id)
    assert (response.status_code, response.json()["error"]["code"]) == (status, code)
    assert (listed, stored_files(tmp_path)) == ([], [])


def test_upload_without_a_file_field_is_invalid(client):
    source_id = create_source(client)
    response = client.post(f"/api/v1/sources/{source_id}/documents", data={"note": "x"})
    assert (response.status_code, response.json()["error"]["code"]) == (422, "invalid_input")


def test_foreign_and_missing_sources_are_indistinguishable(client, settings, services):
    other = Identity(f"other-{uuid4()}", uuid4())
    with TestClient(create_app(settings, services, other)) as foreign_client:
        foreign = create_source(foreign_client, "Private")
        foreign_job = upload(foreign_client, foreign, "secret.md", "# S\n\nSecret.").json()[
            "job_id"
        ]
    for source_id in (foreign, str(uuid4())):
        listed = client.get(f"/api/v1/sources/{source_id}/documents")
        uploaded = upload(client, source_id, "x.md", "# X\n\nText.")
        assert (listed.status_code, uploaded.status_code) == (404, 404)
    job_status = client.get(f"/api/v1/jobs/{foreign_job}").status_code
    assert (job_status, client.get("/api/v1/sources").json()["items"]) == (404, [])


def test_reader_role_can_query_but_not_change_sources(client, engine, identity, worker):
    source_id = create_source(client)
    upload(client, source_id, "guide.md", "# Guide\n\nReaders can search this.")
    drain(worker)
    with engine.begin() as connection:
        connection.execute(
            update(memberships)
            .where(
                memberships.c.workspace_id == identity.workspace_id,
                memberships.c.subject == identity.subject,
            )
            .values(role="reader")
        )
    created = client.post("/api/v1/sources", json={"name": "New"})
    uploaded = upload(client, source_id, "more.md", "# More\n\nText.")
    removed = client.delete(f"/api/v1/sources/{source_id}")
    conversation = client.post("/api/v1/assistant/conversations", json={}).json()["id"]
    answer = ask(client, conversation, "What can readers search?")
    assert [item.status_code for item in (created, uploaded, removed)] == [403, 403, 403]
    assert created.json()["error"]["code"] == "forbidden"
    assert answer.json()["assistant_message"]["answer"]["status"] == "answered"


def stored_vectors(services, identity, source_id):
    vector = services.embeddings.embed(["Removable OIDC fact"])[0]
    return len(services.vectors.search(vector, identity.workspace_id, (UUID(source_id),), 10))


def stored_files(directory):
    return [path for path in directory.rglob("*") if path.is_file()]


def ask_fresh(client, question):
    conversation = client.post("/api/v1/assistant/conversations", json={}).json()["id"]
    return ask(client, conversation, question).json()["assistant_message"]["answer"]


def test_deleting_a_document_removes_it_immediately_and_cleans_up_later(
    client, worker, services, identity, tmp_path
):
    source_id = create_source(client)
    upload(client, source_id, "guide.md", "# Guide\n\nRemovable OIDC fact.")
    drain(worker)
    [document] = documents(client, source_id)
    accepted = client.delete(f"/api/v1/documents/{document['id']}")
    immediately = (
        accepted.status_code,
        documents(client, source_id),
        stored_vectors(services, identity, source_id),
        ask_fresh(client, "OIDC fact")["status"],
    )
    assert immediately == (202, [], 1, "insufficient_evidence")
    drain(worker)
    job = client.get(f"/api/v1/jobs/{accepted.json()['job_id']}").json()
    cleaned = (job["status"], stored_vectors(services, identity, source_id), stored_files(tmp_path))
    assert cleaned == ("succeeded", 0, [])


def test_deleting_a_source_hides_its_documents_and_catalog_entry(client, worker):
    source_id = create_source(client)
    upload(client, source_id, "guide.md", "# Guide\n\nText.")
    drain(worker)
    deleted = client.delete(f"/api/v1/sources/{source_id}").status_code
    listed = client.get("/api/v1/sources").json()["items"]
    documents_status = client.get(f"/api/v1/sources/{source_id}/documents").status_code
    conversation = client.post("/api/v1/assistant/conversations", json={}).json()["id"]
    scoped = ask(client, conversation, "Text", source_ids=[source_id]).status_code
    assert (deleted, listed, documents_status, scoped) == (202, [], 404, 404)
    drain(worker)
