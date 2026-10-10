"""Hybrid retrieval: vector and keyword candidates, fused by rank, hydrated from
PostgreSQL (which re-checks access), then reranked by query-term coverage."""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from app.data.qdrant import QdrantVectorIndex
from app.data.retrieval import RetrievalRepository
from app.services.models import EmbeddingModel, Evidence, Identity, Passage, RetrievalResult
from app.utils.errors import VectorIndexUnavailable

RRF_K = 60
COVERAGE_WEIGHT = 0.3
WORD = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    "the and for are was were with that this from what which who whom how why when where "
    "does did has have had not but our your their its into about than then them they you "
    "can could should would will shall may might must any all some been being also there".split()
)


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
        vectors: QdrantVectorIndex,
        store: RetrievalRepository,
        limits: RetrievalLimits = RetrievalLimits(),
    ):
        self._embeddings = embeddings
        self._vectors = vectors
        self._store = store
        self._limits = limits

    def search(
        self,
        identity: Identity,
        source_ids: tuple[UUID, ...],
        queries: tuple[str, ...],
        round_number: int,
    ) -> RetrievalResult:
        """When the vector index is down, keyword search still answers."""
        if not source_ids or not queries:
            return RetrievalResult((), 0, True)
        limits = self._limits
        try:
            vectors = self._embeddings.embed(queries)
            rankings = [
                self._vectors.search(
                    vector, identity.workspace_id, source_ids, limits.vector_candidates
                )
                for vector in vectors
            ]
            vector_available = True
        except VectorIndexUnavailable:
            rankings, vector_available = [], False
        for query in queries:
            rankings.append(
                self._store.search(identity, source_ids, query, limits.lexical_candidates)
            )
        fused = reciprocal_rank_fusion(rankings)
        strongest = sorted(fused, key=lambda item: (-fused[item], str(item)))[: limits.hydrated]
        passages = self._store.hydrate(identity, source_ids, tuple(strongest))
        ranked = rerank(queries, passages, fused)[: limits.results]
        evidence = tuple(Evidence(passage, score, round_number) for passage, score in ranked)
        return RetrievalResult(evidence, len(fused), vector_available)


def reciprocal_rank_fusion(rankings: Iterable[Sequence[UUID]], k: int = RRF_K) -> dict[UUID, float]:
    """Combine ranks from independent engines; ranks start at 1 and absent ranks add 0."""
    scores: dict[UUID, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1 / (k + rank)
    return scores


def rerank(
    queries: tuple[str, ...], passages: tuple[Passage, ...], fused: dict[UUID, float]
) -> tuple[tuple[Passage, float], ...]:
    """Deterministic baseline: blends normalized fused rank with query-term coverage."""
    terms = query_terms(" ".join(queries))
    top = max((fused[passage.chunk_id] for passage in passages), default=1.0)
    scored = []
    for passage in passages:
        heading = " ".join(passage.locator.heading_path)
        coverage = term_coverage(terms, f"{passage.title} {heading} {passage.text}")
        rank_signal = fused[passage.chunk_id] / top
        score = round((1 - COVERAGE_WEIGHT) * rank_signal + COVERAGE_WEIGHT * coverage, 6)
        scored.append((passage, score))
    return tuple(sorted(scored, key=lambda item: (-item[1], str(item[0].chunk_id))))


def query_terms(text: str) -> frozenset[str]:
    return frozenset(
        word for word in WORD.findall(text.casefold()) if len(word) > 2 and word not in STOPWORDS
    )


def term_coverage(terms: frozenset[str], text: str) -> float:
    if not terms:
        return 0.0
    return len(terms & set(WORD.findall(text.casefold()))) / len(terms)
