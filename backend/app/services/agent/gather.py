"""One retrieval round over the selected, already authorized sources."""

from uuid import UUID

from app.services.agent.budget import BudgetedModel
from app.services.agent.context import merged_evidence
from app.services.agent.policy import AgentPolicy
from app.services.agent.state import AgentState, normalized_query
from app.services.agent.wording import counted
from app.services.ports.retrieval import EvidenceSearch, RetrievalResult


class EvidenceGatherer:
    def __init__(self, retriever: EvidenceSearch, model: BudgetedModel, policy: AgentPolicy):
        self._retriever = retriever
        self._model = model
        self._policy = policy

    def retrieve(self, state: AgentState) -> AgentState:
        self._model.require_time(state)
        round_number = state.rounds + 1
        result = self._retriever.search(state.identity, state.selected, state.queries, round_number)
        return state.traced(
            "retrieve",
            _summary(state, result, round_number),
            rounds=round_number,
            searched=state.searched | frozenset(state.selected),
            asked=state.asked | {normalized_query(query) for query in state.queries},
            evidence=merged_evidence(state.evidence, result.evidence, self._policy.max_evidence),
            consulted=state.consulted | {item.passage.document_id for item in result.evidence},
        )


def _summary(state: AgentState, result: RetrievalResult, round_number: int) -> str:
    summary = (
        f"Round {round_number}: searched {_names(state, state.selected)} with "
        f"{counted(len(state.queries), 'query', 'queries')}; "
        f"{counted(len(result.evidence), 'passage')} passed access checks."
    )
    if not result.vector_available:
        summary += " Vector search was unavailable, so only keyword search ran."
    return summary


def _names(state: AgentState, source_ids: tuple[UUID, ...]) -> str:
    names = [state.source_name(source_id) for source_id in source_ids[:3]]
    extra = len(source_ids) - len(names)
    if extra > 0:
        names.append(f"{extra} more")
    return ", ".join(names)
