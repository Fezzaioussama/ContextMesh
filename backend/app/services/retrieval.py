"""Hybrid retrieval: typed queries go to the engine that suits them (questions and the
hypothetical answer to vector search, keywords to full-text search), ranks are fused with
per-engine weights, candidates are hydrated from PostgreSQL (which re-checks access), then
reranked by the rerank model, or by query-term coverage when that model is off or fails."""

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from app.data.qdrant import QdrantVectorIndex
from app.data.retrieval import RetrievalRepository
from app.services.models import (
    EmbeddingModel,
    Evidence,
    Identity,
    Passage,
    RerankModel,
    RetrievalResult,
    SearchQueries,
)
from app.utils.errors import ContextMeshError, VectorIndexUnavailable

logger = logging.getLogger(__name__)

RRF_K = 60
COVERAGE_WEIGHT = 0.3
WORD = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    "the and for are was were with that this from what which who whom how why when where "
    "does did has have had not but our your their its into about than then them they you "
    "can could should would will shall may might must any all some been being also there".split()
)


@dataclass(frozen=True)
class RetrievalPolicy:
    vector_candidates: int = 50
    lexical_candidates: int = 50
    hydrated: int = 40
    results: int = 16
    # RRF weight per ranking. The hypothetical answer is invented text: it widens recall
    # but should not outrank passages that match what the user actually asked.
    vector_weight: float = 1.0
    keyword_weight: float = 1.0
    hypothetical_weight: float = 0.5


class HybridRetriever:
    def __init__(
        self,
        embeddings: EmbeddingModel,
        vectors: QdrantVectorIndex,
        store: RetrievalRepository,
        reranker: RerankModel | None = None,
        policy: RetrievalPolicy = RetrievalPolicy(),
    ):
        self._embeddings = embeddings
        self._vectors = vectors
        self._store = store
        self._reranker = reranker
        self._policy = policy

    def search(
        self,
        identity: Identity,
        source_ids: tuple[UUID, ...],
        queries: SearchQueries,
        round_number: int,
    ) -> RetrievalResult:
        """Each engine falls back to the other's queries when its own are missing; when
        the vector index is down, keyword search runs every written query."""
        if not source_ids or not queries.texts:
            return RetrievalResult((), 0, True)
        policy = self._policy
        semantic = queries.questions or queries.keywords
        lexical = queries.keywords or queries.questions
        weights = [policy.vector_weight] * len(semantic)
        rankings: list[tuple[float, Sequence[UUID]]] = []
        try:
            vectors = self._embeddings.embed(semantic, "search_query")
            if queries.hypothetical:  # A pseudo-passage, so it is matched as a document.
                vectors += self._embeddings.embed((queries.hypothetical,), "search_document")
                weights.append(policy.hypothetical_weight)
            for weight, vector in zip(weights, vectors, strict=True):
                found = self._vectors.search(
                    vector, identity.workspace_id, source_ids, policy.vector_candidates
                )
                rankings.append((weight, found))
            vector_available = True
        except VectorIndexUnavailable:
            rankings, vector_available = [], False
            lexical = queries.texts
        for query in lexical:
            found = self._store.search(identity, source_ids, query, policy.lexical_candidates)
            rankings.append((policy.keyword_weight, found))
        fused = reciprocal_rank_fusion(rankings)
        strongest = sorted(fused, key=lambda item: (-fused[item], str(item)))[: policy.hydrated]
        passages = self._store.hydrate(identity, source_ids, tuple(strongest))
        ranked = self._ranked(queries.texts, passages, fused)[: policy.results]
        evidence = tuple(Evidence(passage, score, round_number) for passage, score in ranked)
        return RetrievalResult(evidence, len(fused), vector_available)

    def _ranked(
        self, queries: tuple[str, ...], passages: tuple[Passage, ...], fused: dict[UUID, float]
    ) -> tuple[tuple[Passage, float], ...]:
        """The rerank model's relevance scores; the deterministic baseline when the model
        is off or fails, so a rerank outage never fails the turn."""
        reranker = self._reranker
        if passages and reranker is not None and reranker.configured:
            documents = [rerank_text(passage) for passage in passages]
            try:
                scores = reranker.rerank("\n".join(queries), documents)
            except ContextMeshError:
                logger.warning("Rerank model unavailable; ranked by query-term coverage.")
            else:
                scored = zip(passages, scores, strict=True)
                return tuple(sorted(scored, key=lambda item: (-item[1], str(item[0].chunk_id))))
        return rerank(queries, passages, fused)


def reciprocal_rank_fusion(
    rankings: Iterable[tuple[float, Sequence[UUID]]], k: int = RRF_K
) -> dict[UUID, float]:
    """Combine weighted ranks from independent searches; ranks start at 1 and absent
    ranks add 0, so an item found by several searches outscores one found by a single
    search at the same rank."""
    scores: dict[UUID, float] = {}
    for weight, ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + weight / (k + rank)
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


def rerank_text(passage: Passage) -> str:
    """What the rerank model reads: the document title and heading path, then the text."""
    return f"{' > '.join((passage.title, *passage.locator.heading_path))}\n\n{passage.text}"


def query_terms(text: str) -> frozenset[str]:
    return frozenset(
        word for word in WORD.findall(text.casefold()) if len(word) > 2 and word not in STOPWORDS
    )


def term_coverage(terms: frozenset[str], text: str) -> float:
    if not terms:
        return 0.0
    return len(terms & set(WORD.findall(text.casefold()))) / len(terms)
