"""Assess evidence coverage and choose a bounded expansion or stop searching."""

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from app.services.agent.budget import BudgetedModel
from app.services.agent.context import catalog_payload, passages_payload, selected_context
from app.services.agent.instructions import ASSESS, ASSESS_SCHEMA
from app.services.agent.output import flag, known_sources, string_list
from app.services.agent.policy import AgentPolicy
from app.services.agent.state import AgentState, normalized_query
from app.services.agent.wording import counted


@dataclass(frozen=True)
class Assessment:
    sufficient: bool
    gaps: tuple[str, ...]
    queries: tuple[str, ...]
    sources: tuple[UUID, ...]


class EvidenceAssessor:
    def __init__(self, model: BudgetedModel, policy: AgentPolicy):
        self._model = model
        self._policy = policy

    def assess(self, state: AgentState) -> AgentState:
        if not self._may_search_again(state):
            summary = "Search budget reached; answering with the evidence found."
            return state.traced("assess", summary, ready=True)
        if not state.evidence:
            return _expand_without_evidence(state)
        content = self._content(state)
        reply = self._model.call(state, "evidence_assessment", ASSESS, content, ASSESS_SCHEMA)
        return _applied(state.charged(reply.usage), self._parsed(reply.data, state))

    def _may_search_again(self, state: AgentState) -> bool:
        reserve = self._policy.answer_reserve_seconds + self._policy.round_reserve_seconds
        return state.rounds < self._policy.max_rounds and self._model.remaining(state) > reserve

    def _content(self, state: AgentState) -> dict[str, object]:
        unsearched = tuple(source for source in state.searchable if source.id in state.unsearched)
        return {
            "question": state.question,
            "passages": passages_payload(selected_context(state.evidence, self._policy)),
            "unsearched_sources": catalog_payload(unsearched),
        }

    def _parsed(self, data: Mapping[str, object], state: AgentState) -> Assessment:
        return Assessment(
            flag(data, "sufficient"),
            string_list(data, "gaps", self._policy.max_gaps, 300),
            self._fresh_queries(data, state),
            _new_sources(data, state),
        )

    def _fresh_queries(self, data: Mapping[str, object], state: AgentState) -> tuple[str, ...]:
        queries = string_list(data, "queries", self._policy.max_query_variants, 300)
        return tuple(query for query in queries if normalized_query(query) not in state.asked)


def _new_sources(data: Mapping[str, object], state: AgentState) -> tuple[UUID, ...]:
    chosen = known_sources(string_list(data, "expand_source_ids", 50, 64), state.searchable)
    return tuple(source_id for source_id in chosen if source_id not in state.searched)


def _expand_without_evidence(state: AgentState) -> AgentState:
    unsearched = state.unsearched
    if not unsearched:
        return state.traced("assess", "No passages matched in any eligible source.", ready=True)
    summary = (
        f"No passages matched; expanding the search to {counted(len(unsearched), 'more source')}."
    )
    return state.traced("expand", summary, selected=unsearched, ready=False)


def _applied(state: AgentState, assessment: Assessment) -> AgentState:
    if assessment.sufficient:
        return state.traced("assess", "The passages cover the question.", ready=True, gaps=())
    if not assessment.queries and not assessment.sources:
        summary = "Evidence is incomplete and no new search strategy remains."
        return state.traced("assess", summary, ready=True, gaps=assessment.gaps)
    return _next_round(state, assessment)


def _next_round(state: AgentState, assessment: Assessment) -> AgentState:
    queries = assessment.queries or state.queries
    sources = _round_sources(state, assessment)
    summary = (
        f"Missing {_first_gap(assessment)}; searching again with "
        f"{counted(len(queries), 'query', 'queries')} across {counted(len(sources), 'source')}."
    )
    return state.traced(
        "expand", summary, selected=sources, queries=queries, gaps=assessment.gaps, ready=False
    )


def _round_sources(state: AgentState, assessment: Assessment) -> tuple[UUID, ...]:
    """New queries revisit searched sources; repeated queries only run on new sources."""
    if not assessment.queries:
        return assessment.sources
    wanted = state.searched | frozenset(assessment.sources)
    return tuple(source.id for source in state.searchable if source.id in wanted)


def _first_gap(assessment: Assessment) -> str:
    if not assessment.gaps:
        return "details"
    return f"“{assessment.gaps[0][:120]}”"
