"""Grounded answer values; citation fields always come from canonical evidence."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

AnswerStatus = Literal["direct", "answered", "partial", "insufficient_evidence", "withheld"]

NO_EVIDENCE_GAP = "No indexed passage in the selected sources answers this question."
UNSPECIFIED_GAP = "The model judged the answer incomplete but did not name the missing detail."
WITHHELD_TEXT = "This answer is unavailable because evidence it relied on was removed."


@dataclass(frozen=True)
class Locator:
    """Lines count within the extracted text of the page or slide when those are set."""

    heading_path: tuple[str, ...]
    line_start: int
    line_end: int
    page: int | None = None
    slide: int | None = None


@dataclass(frozen=True)
class Citation:
    id: str
    number: int
    source_id: UUID
    document_id: UUID
    document_version_id: UUID
    index_generation_id: UUID
    chunk_id: UUID
    title: str
    locator: Locator
    snippet: str
    source_url: str | None = None


@dataclass(frozen=True)
class Claim:
    id: str
    text: str
    citation_ids: tuple[str, ...]


@dataclass(frozen=True)
class Answer:
    status: AnswerStatus
    text: str
    claims: tuple[Claim, ...]
    citations: tuple[Citation, ...]
    gaps: tuple[str, ...]


@dataclass(frozen=True)
class TraceStage:
    stage: str
    summary: str


def insufficient_answer(gaps: tuple[str, ...]) -> Answer:
    reported = gaps or (NO_EVIDENCE_GAP,)
    text = "I could not find enough evidence in the selected sources to answer this question."
    return Answer("insufficient_evidence", text, (), (), reported)


def direct_answer(text: str) -> Answer:
    return Answer("direct", text, (), (), ())


def withheld_answer() -> Answer:
    return Answer("withheld", WITHHELD_TEXT, (), (), ())


def rendered_text(claims: tuple[Claim, ...], citations: tuple[Citation, ...]) -> str:
    numbers = {citation.id: citation.number for citation in citations}
    return " ".join(_claim_text(claim, numbers) for claim in claims)


def _claim_text(claim: Claim, numbers: dict[str, int]) -> str:
    markers = "".join(f"[{numbers[item]}]" for item in claim.citation_ids)
    return f"{claim.text} {markers}"


def snippet(text: str, limit: int = 280) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    cut = compact[:limit].rsplit(" ", 1)[0]
    return f"{cut}…"
