"""Qdrant projection of chunk vectors; payloads hold identifiers, never chunk text."""

from collections.abc import Sequence
from uuid import UUID

from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import ApiException

from app.services.ports.ingestion import VectorPoint
from app.services.rules.errors import VectorIndexUnavailable

FILTER_FIELDS = ("workspace_id", "source_id", "document_id", "generation_id")
FAILURES = (ApiException, ConnectionError, TimeoutError)


class QdrantVectorIndex:
    def __init__(self, client: QdrantClient, collection: str):
        self._client = client
        self._collection = collection
        self._exists = False
        self._indexed = False

    def upsert(self, points: Sequence[VectorPoint]) -> None:
        if not points:
            return
        try:
            self._ensure(len(points[0].vector))
            self._client.upsert(
                self._collection, points=[_point(item) for item in points], wait=True
            )
        except FAILURES:
            raise VectorIndexUnavailable() from None

    def count_generation(self, generation_id: UUID) -> int:
        try:
            result = self._client.count(
                self._collection, count_filter=_matching("generation_id", generation_id), exact=True
            )
        except FAILURES:
            raise VectorIndexUnavailable() from None
        return result.count

    def delete_generation(self, generation_id: UUID) -> None:
        self._delete("generation_id", generation_id)

    def delete_document(self, document_id: UUID) -> None:
        self._delete("document_id", document_id)

    def delete_source(self, source_id: UUID) -> None:
        self._delete("source_id", source_id)

    def search(
        self,
        vector: tuple[float, ...],
        workspace_id: UUID,
        source_ids: tuple[UUID, ...],
        limit: int,
    ) -> tuple[UUID, ...]:
        if not self._available():
            return ()
        try:
            response = self._client.query_points(
                self._collection,
                query=list(vector),
                query_filter=_scope(workspace_id, source_ids),
                limit=limit,
                with_payload=False,
            )
        except FAILURES:
            raise VectorIndexUnavailable() from None
        return tuple(UUID(str(point.id)) for point in response.points)

    def _delete(self, field: str, value: UUID) -> None:
        if not self._available():
            return
        selector = models.FilterSelector(filter=_matching(field, value))
        try:
            self._client.delete(self._collection, points_selector=selector, wait=True)
        except FAILURES:
            raise VectorIndexUnavailable() from None

    def _available(self) -> bool:
        try:
            self._exists = self._exists or self._client.collection_exists(self._collection)
        except FAILURES:
            raise VectorIndexUnavailable() from None
        return self._exists

    def _ensure(self, size: int) -> None:
        if not self._available():
            vectors = models.VectorParams(size=size, distance=models.Distance.COSINE)
            self._client.create_collection(self._collection, vectors_config=vectors)
            self._exists = True
        if not self._indexed:
            self._index_filters()

    def _index_filters(self) -> None:
        """Re-attempted until it succeeds; creating an existing index is harmless."""
        for field in FILTER_FIELDS:
            self._client.create_payload_index(
                self._collection, field, field_schema=models.PayloadSchemaType.KEYWORD
            )
        self._indexed = True


def _point(item: VectorPoint) -> models.PointStruct:
    return models.PointStruct(
        id=str(item.chunk_id),
        vector=list(item.vector),
        payload={
            "workspace_id": str(item.workspace_id),
            "source_id": str(item.source_id),
            "document_id": str(item.document_id),
            "generation_id": str(item.generation_id),
        },
    )


def _matching(field: str, value: UUID) -> models.Filter:
    return models.Filter(
        must=[models.FieldCondition(key=field, match=models.MatchValue(value=str(value)))]
    )


def _scope(workspace_id: UUID, source_ids: tuple[UUID, ...]) -> models.Filter:
    return models.Filter(
        must=[
            models.FieldCondition(
                key="workspace_id", match=models.MatchValue(value=str(workspace_id))
            ),
            models.FieldCondition(
                key="source_id", match=models.MatchAny(any=[str(item) for item in source_ids])
            ),
        ]
    )
