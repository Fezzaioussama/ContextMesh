"""Workflow nodes and routing predicates; a graph adapter wires these transitions."""

from app.services.agent.assessor import EvidenceAssessor
from app.services.agent.budget import BudgetedModel
from app.services.agent.checker import SupportChecker
from app.services.agent.gather import EvidenceGatherer
from app.services.agent.planner import SearchPlanner
from app.services.agent.policy import AgentPolicy
from app.services.agent.release import released
from app.services.agent.state import AgentState, Draft
from app.services.agent.writer import AnswerWriter
from app.services.rules.knowledge import CatalogSource
from app.services.rules.models import AgentOutcome, Message
from app.utils.security import Identity


class AgentSteps:
    def __init__(
        self,
        model: BudgetedModel,
        planner: SearchPlanner,
        gatherer: EvidenceGatherer,
        assessor: EvidenceAssessor,
        writer: AnswerWriter,
        checker: SupportChecker,
        policy: AgentPolicy,
    ):
        self._model = model
        self._planner = planner
        self._gatherer = gatherer
        self._assessor = assessor
        self._writer = writer
        self._checker = checker
        self._policy = policy

    def start(
        self,
        identity: Identity,
        question: str,
        history: tuple[Message, ...],
        catalog: tuple[CatalogSource, ...],
    ) -> AgentState:
        return AgentState(identity, question, history, catalog, self._model.deadline())

    def has_sources(self, state: AgentState) -> bool:
        return bool(state.searchable)

    def plan(self, state: AgentState) -> AgentState:
        return self._planner.plan(state)

    def retrieve(self, state: AgentState) -> AgentState:
        return self._gatherer.retrieve(state)

    def assess(self, state: AgentState) -> AgentState:
        return self._assessor.assess(state)

    def should_search(self, state: AgentState) -> bool:
        return not state.ready

    def generate(self, state: AgentState) -> AgentState:
        return self._writer.draft(state)

    def has_claims(self, state: AgentState) -> bool:
        return state.draft is not None and bool(state.draft.claims)

    def verify(self, state: AgentState) -> AgentState:
        return self._checker.check(state, _draft(state))

    def should_repair(self, state: AgentState) -> bool:
        repairable = bool(state.unsupported) and state.repairs < self._policy.max_repairs
        return repairable and self._model.remaining(state) > self._policy.answer_reserve_seconds

    def repair(self, state: AgentState) -> AgentState:
        return self._writer.repair(state, _draft(state))

    def release(self, state: AgentState) -> AgentOutcome:
        return released(state, self._policy)


def _draft(state: AgentState) -> Draft:
    if state.draft is None:
        raise RuntimeError("Verification requires a drafted answer.")
    return state.draft
