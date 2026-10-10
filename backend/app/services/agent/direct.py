"""Citation-free answer generation for requests that do not need retrieval."""

from app.services.agent.budget import BudgetedModel
from app.services.agent.context import conversation_payload
from app.services.agent.instructions import DIRECT, DIRECT_SCHEMA
from app.services.agent.output import text_field
from app.services.agent.policy import AgentPolicy
from app.services.agent.state import AgentState
from app.services.rules.errors import model_output_invalid


class DirectAnswerer:
    def __init__(self, model: BudgetedModel, policy: AgentPolicy):
        self._model = model
        self._policy = policy

    def answer(self, state: AgentState) -> AgentState:
        content = {
            "question": state.request,
            "conversation": conversation_payload(state.history, self._policy.history_messages),
        }
        reply = self._model.call(state, "direct_answer", DIRECT, content, DIRECT_SCHEMA)
        answer = text_field(reply.data, "answer", 8_000)
        if not answer:
            raise model_output_invalid()
        summary = "Generated a citation-free direct response."
        return state.charged(reply.usage).traced("direct", summary, direct_response=answer)
