"""Rank fusion, query routing between engines, and the deterministic reranker baseline."""

from uuid import uuid4

import pytest
from app.services.models import Identity, Locator, Passage, SearchQueries
from app.services.retrieval import (
    HybridRetriever,
    query_terms,
    reciprocal_rank_fusion,
    rerank,
)
from app.utils.errors import VectorIndexUnavailable


def test_rrf_uses_one_based_ranks_and_missing_ranks_add_nothing():
    a, b, c = uuid4(), uuid4(), uuid4()
    scores = reciprocal_rank_fusion([(1.0, (a, b)), (1.0, (b, c))], k=60)
    assert (scores[a], scores[b], scores[c]) == pytest.approx((1 / 61, 1 / 62 + 1 / 61, 1 / 62))
    assert max(scores, key=scores.get) == b


def test_rrf_scales_each_ranking_by_its_weight():
    a, b = uuid4(), uuid4()
    scores = reciprocal_rank_fusion([(1.0, (a,)), (0.5, (b, a))], k=60)
    assert (scores[a], scores[b]) == pytest.approx((1 / 61 + 0.5 / 62, 0.5 / 61))


def test_query_terms_ignore_stopwords_and_short_words():
    assert query_terms("What did we decide about the OIDC rollout?") == frozenset(
        {"decide", "oidc", "rollout"}
    )


def passage(text, heading=()):
    return Passage(
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        "Source",
        "doc.md",
        Locator(heading, 1, 1),
        text,
    )


def test_reranker_promotes_term_coverage_among_equal_fused_scores():
    weak = passage("Unrelated paragraph about lunch.")
    strong = passage("The OIDC rollout starts in March.")
    fused = {weak.chunk_id: 0.5, strong.chunk_id: 0.5}
    ranked = rerank(("OIDC rollout",), (weak, strong), fused)
    assert [item.chunk_id for item, _ in ranked] == [strong.chunk_id, weak.chunk_id]


def test_reranker_is_deterministic_for_ties():
    first, second = passage("same text"), passage("same text")
    fused = {first.chunk_id: 0.4, second.chunk_id: 0.4}
    assert rerank(("q",), (first, second), fused) == rerank(("q",), (second, first), fused)


# Query routing between engines


class RecordingEmbeddings:
    def __init__(self):
        self.texts = []

    def embed(self, texts):
        self.texts.extend(texts)
        return tuple((float(index),) for index, _ in enumerate(texts))


class RecordingVectors:
    def __init__(self, available=True):
        self.available = available

    def search(self, vector, workspace_id, source_ids, limit):
        if not self.available:
            raise VectorIndexUnavailable()
        return ()


class RecordingStore:
    """Keyword search returns every passage in the given order."""

    def __init__(self, passages=()):
        self.passages = {item.chunk_id: item for item in passages}
        self.queries = []

    def search(self, identity, source_ids, query, limit):
        self.queries.append(query)
        return tuple(self.passages)

    def hydrate(self, identity, source_ids, chunk_ids):
        return tuple(self.passages[item] for item in chunk_ids if item in self.passages)


def routed(queries, passages=(), vectors_available=True):
    embeddings, store = RecordingEmbeddings(), RecordingStore(passages)
    retriever = HybridRetriever(embeddings, RecordingVectors(vectors_available), store)
    result = retriever.search(Identity("ranking-test", uuid4()), (uuid4(),), queries, 1)
    return embeddings.texts, store.queries, result


def test_questions_and_hypothetical_answer_reach_vectors_and_keywords_reach_full_text():
    queries = SearchQueries(("When does the rollout start?",), ("OIDC rollout",), "In March.")
    vector_texts, keyword_texts, _ = routed(queries)
    assert (vector_texts, keyword_texts) == (
        ["When does the rollout start?", "In March."],
        ["OIDC rollout"],
    )


@pytest.mark.parametrize(
    ("queries", "expected"),
    [
        (SearchQueries(("a question",)), (["a question"], ["a question"])),
        (SearchQueries((), ("keywords",)), (["keywords"], ["keywords"])),
    ],
    ids=["no-keywords", "no-questions"],
)
def test_each_engine_falls_back_to_the_other_kind_of_query(queries, expected):
    vector_texts, keyword_texts, _ = routed(queries)
    assert (vector_texts, keyword_texts) == expected


def test_vector_outage_runs_every_written_query_but_never_the_hypothetical_answer():
    queries = SearchQueries(("a question",), ("keywords",), "Invented answer.")
    _, keyword_texts, result = routed(queries, vectors_available=False)
    assert (keyword_texts, result.vector_available) == (["a question", "keywords"], False)


def test_coverage_ignores_words_only_in_the_hypothetical_answer():
    lunch, rollout = passage("Lunch is at noon."), passage("The rollout starts in March.")
    queries = SearchQueries((), ("march rollout",), "Lunch at noon, menu.")
    _, _, result = routed(queries, passages=(lunch, rollout))
    assert [item.passage.chunk_id for item in result.evidence] == [
        rollout.chunk_id,
        lunch.chunk_id,
    ]
