"""LangGraph adapter: explicit bounded transitions over application agent steps."""

from collections.abc import Callable
from typing import NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph

from app.core.security import Identity
from app.domain.knowledge import CatalogSource
from app.domain.models import AgentOutcome, Message
from app.services.agent.state import AgentState
from app.services.agent.steps import AgentSteps

RECURSION_LIMIT = 30


class GraphState(TypedDict):
    state: AgentState
    outcome: NotRequired[AgentOutcome]


class LangGraphWorkflow:
    """Policy and budgets live in AgentSteps; the graph only sequences them."""

    def __init__(self, steps: AgentSteps):
        self._steps = steps
        self._graph = build_graph(steps)

    def run(
        self,
        identity: Identity,
        question: str,
        history: tuple[Message, ...],
        catalog: tuple[CatalogSource, ...],
    ) -> AgentOutcome:
        initial = self._steps.start(identity, question, history, catalog)
        result = self._graph.invoke({"state": initial}, config={"recursion_limit": RECURSION_LIMIT})
        return result["outcome"]


def build_graph(steps: AgentSteps):
    graph = StateGraph(GraphState)
    for name, step in (
        ("plan", steps.plan),
        ("retrieve", steps.retrieve),
        ("assess", steps.assess),
        ("generate", steps.generate),
        ("verify", steps.verify),
        ("repair", steps.repair),
    ):
        graph.add_node(name, _node(step))
    graph.add_node("release", _release(steps.release))
    _branch(graph, START, steps.has_sources, "plan", "release")
    graph.add_edge("plan", "retrieve")
    graph.add_edge("retrieve", "assess")
    _branch(graph, "assess", steps.should_search, "retrieve", "generate")
    _branch(graph, "generate", steps.has_claims, "verify", "release")
    _branch(graph, "verify", steps.should_repair, "repair", "release")
    _branch(graph, "repair", steps.has_claims, "verify", "release")
    graph.add_edge("release", END)
    return graph.compile()


def _node(step: Callable[[AgentState], AgentState]) -> Callable[[GraphState], dict]:
    return lambda graph_state: {"state": step(graph_state["state"])}


def _release(step: Callable[[AgentState], AgentOutcome]) -> Callable[[GraphState], dict]:
    return lambda graph_state: {"outcome": step(graph_state["state"])}


def _branch(
    graph: StateGraph,
    source: str,
    predicate: Callable[[AgentState], bool],
    chosen: str,
    otherwise: str,
) -> None:
    def route(graph_state: GraphState) -> str:
        return chosen if predicate(graph_state["state"]) else otherwise

    graph.add_conditional_edges(source, route, [chosen, otherwise])
