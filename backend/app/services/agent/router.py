"""Typed, conservative routing before the grounded retrieval workflow."""

from app.services.agent.budget import BudgetedModel
from app.services.agent.context import conversation_payload
from app.services.agent.instructions import ROUTE
from app.services.agent.policy import AgentPolicy
from app.services.agent.state import AgentState
from app.services.ports.decisions import ChoiceReply, ChoiceTask, DecisionModel
from app.utils.exceptions import ContextMeshError

CRITERIA = {
    "direct": (
        "Greeting, general conversation, supplied-text transformation, creative work, "
        "calculation, or stable reasoning that needs no external or private facts."
    ),
    "retrieve": (
        "Indexed resource, source, document, citation, current information, private fact, "
        "or project-specific knowledge is needed; also use when uncertain."
    ),
}


class DecisionRouter:
    def __init__(
        self,
        decision: DecisionModel | None,
        model: BudgetedModel,
        policy: AgentPolicy,
    ):
        self._decision = decision
        self._model = model
        self._policy = policy

    def route(self, state: AgentState) -> AgentState:
        decision = self._decision
        if decision is None or not decision.configured:
            return _retrieval_fallback(state, "The route classifier is unavailable.")
        task = self._task(state)
        try:
            reply = decision.choose(task)
        except ContextMeshError:
            return _retrieval_fallback(state, "The route classifier failed safely.")
        return self._apply(state, reply)

    def _task(self, state: AgentState) -> ChoiceTask:
        remaining = self._model.require_time(state)
        timeout = min(self._policy.call_timeout_seconds, remaining)
        context = conversation_payload(state.history, self._policy.history_messages)
        return ChoiceTask(
            "retrieval_route",
            {"question": state.request, "conversation": context},
            ROUTE,
            CRITERIA,
            timeout,
        )

    def _apply(self, state: AgentState, reply: ChoiceReply) -> AgentState:
        routed = "direct" if self._confident_direct(reply) else "retrieve"
        summary = _summary(routed, reply.choice)
        return state.charged(reply.usage).traced("route", summary, route=routed)

    def _confident_direct(self, reply: ChoiceReply) -> bool:
        direct_probability = reply.probabilities.get("direct", 0.0)
        return (
            reply.choice.strip().casefold() == "direct"
            and reply.confidence >= self._policy.direct_route_confidence
            and direct_probability >= self._policy.direct_route_confidence
        )


def _retrieval_fallback(state: AgentState, reason: str) -> AgentState:
    return state.traced("route", f"{reason} Defaulted to retrieval.", route="retrieve")


def _summary(route: str, choice: str) -> str:
    if route == "direct":
        return "The classifier confidently selected a direct response."
    if choice.strip().casefold() not in CRITERIA:
        return "The classifier returned an unknown route; defaulted to retrieval."
    return "The classifier was not confident enough for a direct response; using retrieval."
