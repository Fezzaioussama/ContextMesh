"""Hybrid retrieval: scoped candidates, canonical hydration, rank fusion, reranking."""

from dataclasses import dataclass
from uuid import UUID

from app.services.ports.models import EmbeddingModel
from app.services.ports.retrieval import (
    LexicalSearch,
    PassageStore,
    Reranker,
    RetrievalResult,
    VectorSearch,
)
from app.services.rules.errors import VectorIndexUnavailable
from app.services.rules.knowledge import Evidence, Passage
from app.services.rules.ranking import query_terms, reciprocal_rank_fusion, term_coverage
from app.utils.security import Identity


@dataclass(frozen=True)
class RetrievalLimits:
    vector_candidates: int = 50
    lexical_candidates: int = 50
    hydrated: int = 40
    results: int = 10


class HybridRetriever:
    def __init__(
        self,
        embeddings: EmbeddingModel,
        vectors: VectorSearch,
        lexical: LexicalSearch,
        passages: PassageStore,
        reranker: Reranker,
        limits: RetrievalLimits = RetrievalLimits(),
    ):
        self._embeddings = embeddings
        self._vectors = vectors
        self._lexical = lexical
        self._passages = passages
        self._reranker = reranker
        self._limits = limits

    def search(
        self,
        identity: Identity,
        source_ids: tuple[UUID, ...],
        queries: tuple[str, ...],
        round_number: int,
    ) -> RetrievalResult:
        if not source_ids or not queries:
            return RetrievalResult((), 0, True)
        fused, available = self._fused(identity, source_ids, queries)
        candidates = _strongest(fused, self._limits.hydrated)
        passages = self._passages.hydrate(identity, source_ids, candidates)
        ranked = self._reranker.rank(queries, passages, fused)[: self._limits.results]
        return RetrievalResult(_evidence(ranked, round_number), len(fused), available)

    def _fused(
        self, identity: Identity, source_ids: tuple[UUID, ...], queries: tuple[str, ...]
    ) -> tuple[dict[UUID, float], bool]:
        vector, available = self._vector_rankings(identity.workspace_id, source_ids, queries)
        lexical = [self._lexical_ranking(identity, source_ids, query) for query in queries]
        return reciprocal_rank_fusion([*vector, *lexical]), available

    def _lexical_ranking(
        self, identity: Identity, source_ids: tuple[UUID, ...], query: str
    ) -> tuple[UUID, ...]:
        return self._lexical.search(identity, source_ids, query, self._limits.lexical_candidates)

    def _vector_rankings(
        self, workspace_id: UUID, source_ids: tuple[UUID, ...], queries: tuple[str, ...]
    ) -> tuple[list[tuple[UUID, ...]], bool]:
        try:
            vectors = self._embeddings.embed(queries)
            rankings = [
                self._vectors.search(
                    vector, workspace_id, source_ids, self._limits.vector_candidates
                )
                for vector in vectors
            ]
        except VectorIndexUnavailable:
            return [], False
        return rankings, True


def _strongest(fused: dict[UUID, float], limit: int) -> tuple[UUID, ...]:
    ordered = sorted(fused, key=lambda item: (-fused[item], str(item)))
    return tuple(ordered[:limit])


def _evidence(ranked: tuple[tuple[Passage, float], ...], round_number: int) -> tuple[Evidence, ...]:
    return tuple(Evidence(passage, score, round_number) for passage, score in ranked)


class TermOverlapReranker:
    """Deterministic baseline: blends normalized fused rank with query-term coverage."""

    def __init__(self, coverage_weight: float = 0.3):
        self._weight = coverage_weight

    def rank(
        self,
        queries: tuple[str, ...],
        passages: tuple[Passage, ...],
        fused: dict[UUID, float],
    ) -> tuple[tuple[Passage, float], ...]:
        terms = query_terms(" ".join(queries))
        top = max((fused[passage.chunk_id] for passage in passages), default=1.0)
        scored = [(passage, self._score(passage, terms, fused, top)) for passage in passages]
        return tuple(sorted(scored, key=lambda item: (-item[1], str(item[0].chunk_id))))

    def _score(
        self, passage: Passage, terms: frozenset[str], fused: dict[UUID, float], top: float
    ) -> float:
        heading = " ".join(passage.locator.heading_path)
        coverage = term_coverage(terms, f"{passage.title} {heading} {passage.text}")
        rank_signal = fused[passage.chunk_id] / top
        return round((1 - self._weight) * rank_signal + self._weight * coverage, 6)
