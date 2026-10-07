"""Rank fusion and the deterministic reranker baseline."""

from uuid import uuid4

import pytest
from app.domain.answers import Locator
from app.domain.knowledge import Passage
from app.domain.ranking import query_terms, reciprocal_rank_fusion
from app.services.retrieval import TermOverlapReranker


def test_rrf_uses_one_based_ranks_and_missing_ranks_add_nothing():
    a, b, c = uuid4(), uuid4(), uuid4()
    scores = reciprocal_rank_fusion([(a, b), (b, c)], k=60)
    assert (scores[a], scores[b], scores[c]) == pytest.approx((1 / 61, 1 / 62 + 1 / 61, 1 / 62))
    assert max(scores, key=scores.get) == b


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
    ranked = TermOverlapReranker().rank(("OIDC rollout",), (weak, strong), fused)
    assert [item.chunk_id for item, _ in ranked] == [strong.chunk_id, weak.chunk_id]


def test_reranker_is_deterministic_for_ties():
    first, second = passage("same text"), passage("same text")
    fused = {first.chunk_id: 0.4, second.chunk_id: 0.4}
    reranker = TermOverlapReranker()
    assert reranker.rank(("q",), (first, second), fused) == reranker.rank(
        ("q",), (second, first), fused
    )
