"""Rank fusion and lexical-overlap signals over already ranked candidates."""

import re
from collections.abc import Iterable, Sequence
from uuid import UUID

RRF_K = 60
WORD = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    "the and for are was were with that this from what which who whom how why when where "
    "does did has have had not but our your their its into about than then them they you "
    "can could should would will shall may might must any all some been being also there".split()
)


def reciprocal_rank_fusion(rankings: Iterable[Sequence[UUID]], k: int = RRF_K) -> dict[UUID, float]:
    """Combine ranks from independent engines; ranks start at 1 and absent ranks add 0."""
    scores: dict[UUID, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1 / (k + rank)
    return scores


def query_terms(text: str) -> frozenset[str]:
    return frozenset(
        word for word in WORD.findall(text.casefold()) if len(word) > 2 and word not in STOPWORDS
    )


def term_coverage(terms: frozenset[str], text: str) -> float:
    if not terms:
        return 0.0
    return len(terms & set(WORD.findall(text.casefold()))) / len(terms)
