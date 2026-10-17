"""Golden evaluation cases, their JSONL loader, and the deterministic metrics of one run.

A case names its expected documents by title, not id, because ids change when a document
is uploaded again or reindexed. Every metric returns None when it does not apply to the
case, so it never drags a mean down.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from pydantic import ConfigDict, StringConstraints, TypeAdapter, ValidationError
from pydantic.dataclasses import dataclass

from app.services.agent.state import Route, normalized_query
from app.services.models import AgentOutcome, Answer, Evidence, Message, MessageRole

# Quality metrics are 0 to 1, higher is better; budget metrics are raw amounts.
DETERMINISTIC_QUALITY = (
    "route_correct",
    "abstention_correct",
    "document_recall",
    "document_precision",
    "quote_recall",
)
BUDGET_METRICS = ("total_tokens", "latency_seconds", "search_rounds", "repairs")

# A partial answer is acceptable either way: it answers some parts and names its gaps.
ANSWERED = frozenset({"answered", "partial"})
ABSTAINED = frozenset({"insufficient_evidence", "partial"})

Stripped = Annotated[str, StringConstraints(strip_whitespace=True)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
STRICT = ConfigDict(strict=True, extra="forbid")


class InvalidDataset(ValueError):
    """The dataset cannot be evaluated; the message names the file and line."""


@dataclass(frozen=True, config=STRICT)
class HistoryTurn:
    """One earlier message of the conversation the case continues."""

    role: MessageRole
    content: Text


@dataclass(frozen=True, config=STRICT)
class EvalCase:
    """One golden question and what a good run looks like.

    Attributes:
        answerable: Whether the indexed corpus answers the question.
        expected_documents: Titles of the documents a complete answer cites.
        expected_quotes: Text the passages the answer was drafted from must contain.
        reference_answer: A person's answer for the correctness judge; empty skips it.
    """

    id: Text
    question: Text
    expected_route: Route
    answerable: bool
    history: tuple[HistoryTurn, ...] = ()
    expected_documents: tuple[Text, ...] = ()
    expected_quotes: tuple[Text, ...] = ()
    reference_answer: Stripped = ""
    tags: tuple[Text, ...] = ()

    def messages(self) -> tuple[Message, ...]:
        """The history as the conversation messages the agent receives."""
        now = datetime.now(UTC)
        return tuple(
            Message(uuid4(), uuid4(), turn.role, turn.content, now) for turn in self.history
        )


CASE = TypeAdapter(EvalCase)


def load_cases(path: Path) -> tuple[EvalCase, ...]:
    """Read one case per non-blank line of a JSONL file.

    Raises:
        InvalidDataset: The file is not UTF-8 text, a line is not a valid case or repeats a
            case id, or the file holds no case.
        OSError: The file cannot be read.
    """
    cases: dict[str, EvalCase] = {}
    for number, line in _filled_lines(path):
        case = _parsed(line, f"{path}:{number}")
        if case.id in cases:
            raise InvalidDataset(f"{path}:{number}: case id {case.id!r} is already used.")
        cases[case.id] = case
    if not cases:
        raise InvalidDataset(f"{path}: the dataset holds no case.")
    return tuple(cases.values())


def _filled_lines(path: Path) -> list[tuple[int, str]]:
    """The file's non-blank lines, each with its line number counted from 1.

    Raises:
        InvalidDataset: The file is not UTF-8 text.
        OSError: The file cannot be read.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise InvalidDataset(f"{path}: the file is not UTF-8 text.") from None
    numbered = enumerate(text.splitlines(), start=1)
    return [(number, line) for number, line in numbered if line.strip()]


def _parsed(line: str, where: str) -> EvalCase:
    """One validated case; each validation problem is named by its field path."""
    try:
        return CASE.validate_json(line)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(map(str, item['loc'])) or 'line'}: {item['msg']}" for item in error.errors()
        )
        raise InvalidDataset(f"{where}: {problems}") from None


# Deterministic metrics


def route_correct(case: EvalCase, answer: Answer) -> bool:
    """Whether the agent took the expected route; only a direct response is direct."""
    route = "direct" if answer.status == "direct" else "retrieve"
    return route == case.expected_route


def abstention_correct(case: EvalCase, answer: Answer) -> bool | None:
    """Whether the agent answered an answerable question and abstained otherwise.

    Returns:
        None for an answerable direct case, which has no evidence to abstain on.
    """
    if case.answerable and case.expected_route == "direct":
        return None
    accepted = ANSWERED if case.answerable else ABSTAINED
    return answer.status in accepted


def document_recall(case: EvalCase, answer: Answer) -> float | None:
    """The share of expected documents the answer cites; None when none are expected."""
    if not case.expected_documents:
        return None
    expected = _titles(case.expected_documents)
    return len(expected & _cited_titles(answer)) / len(expected)


def document_precision(case: EvalCase, answer: Answer) -> float | None:
    """The share of cited documents that are expected.

    Returns:
        None when the answer cites nothing or the case expects no document.
    """
    cited = _cited_titles(answer)
    if not cited or not case.expected_documents:
        return None
    return len(cited & _titles(case.expected_documents)) / len(cited)


def quote_recall(case: EvalCase, context: tuple[Evidence, ...]) -> float | None:
    """The share of expected quotes found in a passage the answer was drafted from.

    Matching ignores case and whitespace, so a quote survives chunking and line wrapping.

    Returns:
        None when the case expects no quote.
    """
    if not case.expected_quotes:
        return None
    passages = [normalized_query(item.passage.text) for item in context]
    quotes = [normalized_query(quote) for quote in case.expected_quotes]
    found = sum(any(quote in passage for passage in passages) for quote in quotes)
    return found / len(quotes)


def scored(
    case: EvalCase, outcome: AgentOutcome, latency_seconds: float
) -> dict[str, float | None]:
    """Every deterministic metric of one run, keyed by metric name."""
    answer = outcome.answer
    stages = [stage.stage for stage in outcome.trace]
    return {
        "route_correct": route_correct(case, answer),
        "abstention_correct": abstention_correct(case, answer),
        "document_recall": document_recall(case, answer),
        "document_precision": document_precision(case, answer),
        "quote_recall": quote_recall(case, outcome.context),
        "total_tokens": outcome.usage.total,
        "latency_seconds": round(latency_seconds, 3),
        "search_rounds": stages.count("retrieve"),
        "repairs": stages.count("repair"),
    }


def _titles(titles: tuple[str, ...]) -> set[str]:
    """Titles compared the way quotes are: ignoring case and spacing."""
    return {normalized_query(title) for title in titles}


def _cited_titles(answer: Answer) -> set[str]:
    """The distinct titles of the documents the answer cites."""
    return _titles(tuple(citation.title for citation in answer.citations))
