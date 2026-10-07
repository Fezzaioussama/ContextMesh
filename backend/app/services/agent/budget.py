"""Deadline-bounded structured model calls shared by every agent stage."""

import json
from collections.abc import Callable, Mapping

from app.services.agent.policy import AgentPolicy
from app.services.agent.state import AgentState
from app.services.ports.models import ReasoningModel, StructuredReply, StructuredTask
from app.services.rules.errors import agent_budget_exceeded, agent_deadline_exceeded


class BudgetedModel:
    def __init__(self, model: ReasoningModel, policy: AgentPolicy, clock: Callable[[], float]):
        self._model = model
        self._policy = policy
        self._clock = clock

    def deadline(self) -> float:
        return self._clock() + self._policy.deadline_seconds

    def remaining(self, state: AgentState) -> float:
        return state.deadline - self._clock()

    def require_time(self, state: AgentState) -> float:
        """Every model call and retrieval round starts only with usable time left."""
        remaining = self.remaining(state)
        if remaining < self._policy.minimum_call_seconds:
            raise agent_deadline_exceeded()
        return remaining

    def call(
        self,
        state: AgentState,
        name: str,
        instructions: str,
        content: Mapping[str, object],
        schema: Mapping[str, object],
    ) -> StructuredReply:
        remaining = self.require_time(state)
        if state.usage.total >= self._policy.max_total_tokens:
            raise agent_budget_exceeded()
        timeout = min(self._policy.call_timeout_seconds, remaining)
        payload = json.dumps(content, ensure_ascii=False)
        return self._model.complete(StructuredTask(name, instructions, payload, schema, timeout))
