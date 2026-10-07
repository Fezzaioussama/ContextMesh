"""Retrieval boundaries: scoped candidates, canonical hydration, and degraded search."""

from uuid import UUID, uuid4

import pytest
from app.data.db.models.knowledge import chunks
from app.data.db.repositories.retrieval_repository import RetrievalRepository, lexical_expression
from app.services.rules.errors import VectorIndexUnavailable
from app.setup.api import create_app
from app.utils.security import Identity
from fastapi.testclient import TestClient
from sqlalchemy import select
from support import ask, create_source, drain, upload

pytestmark = pytest.mark.integration


def chunk_ids(engine, document_id):
    query = select(chunks.c.id).where(chunks.c.document_id == UUID(document_id))
    with engine.begin() as connection:
        return set(connection.execute(query).scalars())


def test_lexical_candidates_stay_inside_workspace_and_requested_sources(
    client, worker, engine, settings, services, identity
):
    mine = create_source(client, "Mine")
    other_mine = create_source(client, "Other mine")
    upload(client, mine, "a.md", "# A\n\nKey rotation happens weekly.")
    upload(client, other_mine, "b.md", "# B\n\nKey rotation is manual here.")
    stranger = Identity(f"stranger-{uuid4()}", uuid4())
    with TestClient(create_app(settings, services, stranger)) as foreign_client:
        theirs = create_source(foreign_client, "Theirs")
        upload(foreign_client, theirs, "c.md", "# C\n\nKey rotation is secret.")
    drain(worker)
    repository = RetrievalRepository(engine)
    found = repository.search(identity, (UUID(mine),), "key rotation", 10)
    foreign = repository.search(identity, (UUID(theirs),), "key rotation", 10)
    hydrated = repository.hydrate(identity, (UUID(mine), UUID(theirs)), found)
    assert len(found) == 1
    assert foreign == ()
    assert [passage.source_id for passage in hydrated] == [UUID(mine)]


def test_stale_vector_candidates_are_not_hydrated(client, worker, engine, identity):
    source_id = create_source(client)
    document_id = upload(client, source_id, "p.md", "# P\n\nOld text.").json()["document"]["id"]
    drain(worker)
    old = chunk_ids(engine, document_id)
    upload(client, source_id, "p.md", "# P\n\nNew text.")
    drain(worker)
    new = chunk_ids(engine, document_id) - old
    hydrated = RetrievalRepository(engine).hydrate(identity, (UUID(source_id),), (*old, *new))
    assert {passage.chunk_id for passage in hydrated} == new


def test_vector_outage_falls_back_to_keyword_search(
    client, worker, conversation_id, services, monkeypatch
):
    source_id = create_source(client)
    upload(client, source_id, "guide.md", "# Guide\n\nRotation happens weekly.")
    drain(worker)

    def unavailable(*args):
        raise VectorIndexUnavailable()

    monkeypatch.setattr(services.vectors, "search", unavailable)
    body = ask(client, conversation_id, "rotation schedule").json()
    retrieve = body["trace"][1]
    snippet = body["assistant_message"]["answer"]["citations"][0]["snippet"]
    assert (retrieve["stage"], "only keyword search ran" in retrieve["summary"], snippet) == (
        "retrieve",
        True,
        "Rotation happens weekly.",
    )


@pytest.mark.parametrize("query", ["a & b | !c", "it's (quoted) :* <-> rotation", "!!!"])
def test_query_operators_never_reach_the_text_search_parser(client, engine, identity, query):
    source_id = create_source(client)
    assert "&" not in lexical_expression(query)
    assert RetrievalRepository(engine).search(identity, (UUID(source_id),), query, 5) == ()
