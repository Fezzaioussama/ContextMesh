"""The Qdrant adapter against a real server: scoped filters, counts, deletes, outages."""

import os
from uuid import uuid4

import pytest
from app.domain.errors import VectorIndexUnavailable
from app.search.qdrant import QdrantVectorIndex
from app.services.ports.ingestion import VectorPoint
from qdrant_client import QdrantClient

pytestmark = pytest.mark.integration


@pytest.fixture
def index():
    url = os.getenv("CONTEXTMESH_TEST_QDRANT_URL")
    if not url:
        pytest.skip("Set CONTEXTMESH_TEST_QDRANT_URL to run Qdrant server tests.")
    client = QdrantClient(url=url, timeout=5, check_compatibility=False)
    collection = f"contextmesh_test_{uuid4().hex[:12]}"
    yield QdrantVectorIndex(client, collection)
    client.delete_collection(collection)
    client.close()


PROBE = (1.0, 0.0, 0.0)


def point(workspace, source, document, generation, vector=PROBE):
    return VectorPoint(uuid4(), vector, workspace, source, document, generation)


def points(workspace, source, document, generation, count):
    return [point(workspace, source, document, generation) for _ in range(count)]


def test_search_is_filtered_by_workspace_and_sources_on_the_server(index):
    workspace, other_workspace = uuid4(), uuid4()
    wanted, unwanted = uuid4(), uuid4()
    mine = point(workspace, wanted, uuid4(), uuid4())
    excluded_source = point(workspace, unwanted, uuid4(), uuid4())
    foreign = point(other_workspace, wanted, uuid4(), uuid4())
    index.upsert([mine, excluded_source, foreign])
    assert index.search((1.0, 0.0, 0.0), workspace, (wanted,), 10) == (mine.chunk_id,)


def test_generation_counts_and_scoped_deletes(index):
    workspace, source, document, generation = uuid4(), uuid4(), uuid4(), uuid4()
    kept = point(workspace, source, uuid4(), uuid4())
    index.upsert([*points(workspace, source, document, generation, 3), kept])
    before = index.count_generation(generation)
    index.delete_document(document)
    after = (index.count_generation(generation), index.search(PROBE, workspace, (source,), 10))
    index.delete_source(source)
    assert (before, after) == (3, (0, (kept.chunk_id,)))
    assert index.search(PROBE, workspace, (source,), 10) == ()


def test_unreachable_server_is_reported_as_unavailable():
    client = QdrantClient(url="http://127.0.0.1:9", timeout=1, check_compatibility=False)
    index = QdrantVectorIndex(client, "missing")
    with pytest.raises(VectorIndexUnavailable):
        index.search((1.0,), uuid4(), (uuid4(),), 5)
    with pytest.raises(VectorIndexUnavailable):
        index.upsert([point(uuid4(), uuid4(), uuid4(), uuid4(), (1.0,))])
    client.close()
