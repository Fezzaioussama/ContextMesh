"""Build the final answer from supported claims and canonical evidence records."""

from uuid import UUID

from app.domain.answers import (
    UNSPECIFIED_GAP,
    Answer,
    Citation,
    Claim,
    insufficient_answer,
    rendered_text,
    snippet,
)
from app.domain.knowledge import Evidence
from app.domain.models import AgentOutcome
from app.services.agent.policy import AgentPolicy
from app.services.agent.state import AgentState, DraftClaim
from app.services.agent.wording import counted


def released(state: AgentState, policy: AgentPolicy) -> AgentOutcome:
    kept = _supported(state)
    answer = _answer(state, kept, _gaps(state, policy))
    removed = _removed(state, kept)
    summary = (
        f"Released {counted(len(answer.claims), 'supported claim')} with "
        f"{counted(len(answer.citations), 'citation')}."
    )
    if removed:
        summary += f" Removed {counted(removed, 'unsupported claim')}."
    if not answer.claims:
        summary = "No supported answer was found; reported the evidence gap."
    trace = state.traced("release", summary).trace
    return AgentOutcome(answer, trace, state.usage, _consulted(state))


def _consulted(state: AgentState) -> tuple[UUID, ...]:
    """Every document retrieved in any round, including passages later pushed out."""
    return tuple(sorted(state.consulted, key=str))


def _supported(state: AgentState) -> tuple[DraftClaim, ...]:
    if state.draft is None:
        return ()
    unsupported = set(state.unsupported)
    return tuple(c for i, c in enumerate(state.draft.claims) if i not in unsupported)


def _removed(state: AgentState, kept: tuple[DraftClaim, ...]) -> int:
    if state.draft is None:
        return 0
    return len(state.draft.claims) - len(kept)


def _answer(state: AgentState, kept: tuple[DraftClaim, ...], gaps: tuple[str, ...]) -> Answer:
    if not kept:
        return insufficient_answer(gaps)
    citations = _citations(kept)
    claims = _claims(kept, citations)
    status = _status(state, kept, gaps)
    return Answer(status, rendered_text(claims, citations), claims, citations, gaps)


def _status(state: AgentState, kept: tuple[DraftClaim, ...], gaps: tuple[str, ...]) -> str:
    if _drafted_complete(state) and not gaps and _removed(state, kept) == 0:
        return "answered"
    return "partial"


def _drafted_complete(state: AgentState) -> bool:
    return state.draft is not None and state.draft.status == "answered"


def _gaps(state: AgentState, policy: AgentPolicy) -> tuple[str, ...]:
    drafted = () if state.draft is None else state.draft.gaps
    gaps = tuple(dict.fromkeys((*drafted, *state.gaps)))[: policy.max_gaps]
    return gaps or _unexplained_partial(state)


def _unexplained_partial(state: AgentState) -> tuple[str, ...]:
    """A partial verdict without a named gap is reported, never shown unexplained."""
    if state.draft is not None and state.draft.status == "partial":
        return (UNSPECIFIED_GAP,)
    return ()


def _citations(kept: tuple[DraftClaim, ...]) -> tuple[Citation, ...]:
    ordered = dict.fromkeys(item for claim in kept for item in claim.evidence)
    return tuple(_citation(number, item) for number, item in enumerate(ordered, start=1))


def _citation(number: int, item: Evidence) -> Citation:
    passage = item.passage
    return Citation(
        f"citation-{number}",
        number,
        passage.source_id,
        passage.document_id,
        passage.document_version_id,
        passage.index_generation_id,
        passage.chunk_id,
        passage.title,
        passage.locator,
        snippet(passage.text),
    )


def _claims(kept: tuple[DraftClaim, ...], citations: tuple[Citation, ...]) -> tuple[Claim, ...]:
    by_chunk = {citation.chunk_id: citation.id for citation in citations}
    return tuple(
        Claim(f"claim-{index}", claim.text, _citation_ids(claim, by_chunk))
        for index, claim in enumerate(kept, start=1)
    )


def _citation_ids(claim: DraftClaim, by_chunk: dict) -> tuple[str, ...]:
    return tuple(dict.fromkeys(by_chunk[item.passage.chunk_id] for item in claim.evidence))
