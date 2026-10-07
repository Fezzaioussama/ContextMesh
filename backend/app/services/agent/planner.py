"""Plan sources and query variants; selections are validated against the catalog."""

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from app.services.agent.budget import BudgetedModel
from app.services.agent.context import catalog_payload, conversation_payload
from app.services.agent.instructions import PLAN, PLAN_SCHEMA
from app.services.agent.output import known_sources, string_list, text_field
from app.services.agent.policy import AgentPolicy
from app.services.agent.state import AgentState
from app.services.agent.wording import counted


@dataclass(frozen=True)
class SearchPlan:
    question: str
    sources: tuple[UUID, ...]
    queries: tuple[str, ...]


class SearchPlanner:
    def __init__(self, model: BudgetedModel, policy: AgentPolicy):
        self._model = model
        self._policy = policy

    def plan(self, state: AgentState) -> AgentState:
        reply = self._model.call(state, "search_plan", PLAN, self._content(state), PLAN_SCHEMA)
        plan = self._parsed(reply.data, state)
        summary = (
            f"Selected {len(plan.sources)} of {counted(len(state.searchable), 'searchable source')}"
            f" and {counted(len(plan.queries), 'search query', 'search queries')}."
        )
        return state.charged(reply.usage).traced(
            "plan", summary, question=plan.question, selected=plan.sources, queries=plan.queries
        )

    def _content(self, state: AgentState) -> dict[str, object]:
        return {
            "question": state.request,
            "conversation": conversation_payload(state.history, self._policy.history_messages),
            "sources": catalog_payload(state.searchable),
        }

    def _parsed(self, data: Mapping[str, object], state: AgentState) -> SearchPlan:
        question = _question(data, state)
        return SearchPlan(question, _sources(data, state), self._queries(data, question))

    def _queries(self, data: Mapping[str, object], question: str) -> tuple[str, ...]:
        queries = string_list(data, "queries", self._policy.max_query_variants, 300)
        return queries or (question,)


def _question(data: Mapping[str, object], state: AgentState) -> str:
    return text_field(data, "standalone_question", 1000) or state.request


def _sources(data: Mapping[str, object], state: AgentState) -> tuple[UUID, ...]:
    chosen = known_sources(string_list(data, "source_ids", 50, 64), state.searchable)
    return chosen or tuple(source.id for source in state.searchable)
