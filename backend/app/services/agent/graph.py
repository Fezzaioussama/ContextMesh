"""LangGraph adapter: explicit bounded transitions over application agent steps.

The run is one parent graph over three subgraphs, so a trace groups each phase:
`routing` (route, direct), `research` (plan, retrieve ⟲ assess), and `answering`
(generate, verify ⟲ repair); `release` always ends the run.

LangGraph applies `recursion_limit` to each subgraph separately, so the whole-run
bound is a node counter carried in the graph state across all three subgraphs.
"""

from collections.abc import Callable
from contextlib import nullcontext
from typing import NotRequired, TypedDict

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph

from app.services.agent.state import AgentState
from app.services.agent.steps import AgentSteps
from app.services.ports.tracing import RunTracing
from app.services.rules.knowledge import CatalogSource
from app.services.rules.models import AgentOutcome, Message
from app.utils.logging import flow_event, flow_ids
from app.utils.security import Identity

RECURSION_LIMIT = 30  # Node executions per run, release included.
RUN_NAME = "ask_question"

Step = Callable[[AgentState], AgentState]
Predicate = Callable[[AgentState], bool]


class GraphState(TypedDict):
    state: AgentState
    outcome: NotRequired[AgentOutcome]
    steps: NotRequired[int]


class LangGraphWorkflow:
    """Policy and budgets live in AgentSteps; the graph only sequences them."""

    def __init__(self, steps: AgentSteps, tracing: RunTracing | None = None):
        self._steps = steps
        self._tracing = tracing
        self._graph = build_graph(steps)

    def run(
        self,
        identity: Identity,
        question: str,
        history: tuple[Message, ...],
        catalog: tuple[CatalogSource, ...],
    ) -> AgentOutcome:
        initial = self._steps.start(identity, question, history, catalog)
        config = {"recursion_limit": RECURSION_LIMIT, "run_name": RUN_NAME}
        with self._scope(identity):
            result = self._graph.invoke({"state": initial}, config=config)
        return result["outcome"]

    def _scope(self, identity: Identity):
        if self._tracing is None:
            return nullcontext()
        ids = {**flow_ids(), "workspace_id": str(identity.workspace_id)}
        return self._tracing.scope(RUN_NAME, ids)


def build_graph(steps: AgentSteps):
    graph = StateGraph(GraphState)
    graph.add_node("routing", _routing(steps))
    graph.add_node("research", _research(steps))
    graph.add_node("answering", _answering(steps))
    graph.add_node("release", _release(steps.release))
    graph.add_edge(START, "routing")
    graph.add_conditional_edges("routing", _after_routing(steps), ["research", "release"])
    graph.add_edge("research", "answering")
    graph.add_edge("answering", "release")
    graph.add_edge("release", END)
    return graph.compile(name=RUN_NAME)


def _routing(steps: AgentSteps):
    graph = _phase((("route", steps.route), ("direct", steps.answer_direct)))
    _branch(graph, "route", steps.should_answer_direct, "direct", END)
    graph.add_edge("direct", END)
    return graph.compile(name="routing")


def _research(steps: AgentSteps):
    graph = _phase((("plan", steps.plan), ("retrieve", steps.retrieve), ("assess", steps.assess)))
    graph.add_edge("plan", "retrieve")
    graph.add_edge("retrieve", "assess")
    _branch(graph, "assess", steps.should_search, "retrieve", END)
    return graph.compile(name="research")


def _answering(steps: AgentSteps):
    graph = _phase(
        (("generate", steps.generate), ("verify", steps.verify), ("repair", steps.repair))
    )
    _branch(graph, "generate", steps.has_claims, "verify", END)
    _branch(graph, "verify", steps.should_repair, "repair", END)
    _branch(graph, "repair", steps.has_claims, "verify", END)
    return graph.compile(name="answering")


def _phase(nodes: tuple[tuple[str, Step], ...]) -> StateGraph:
    """A subgraph whose first node is its entry; each node logs its flow step."""
    graph = StateGraph(GraphState)
    for name, step in nodes:
        graph.add_node(name, _node(name, step))
    graph.add_edge(START, nodes[0][0])
    return graph


def _after_routing(steps: AgentSteps) -> Callable[[GraphState], str]:
    def next_phase(graph_state: GraphState) -> str:
        state = graph_state["state"]
        if steps.should_answer_direct(state) or not steps.has_sources(state):
            return "release"
        return "research"

    return next_phase


def _node(name: str, step: Step) -> Callable[[GraphState], dict]:
    def run(graph_state: GraphState) -> dict:
        taken = graph_state.get("steps", 0)
        if taken >= RECURSION_LIMIT - 1:  # Leave the last step for release.
            raise GraphRecursionError(f"Agent run exceeded {RECURSION_LIMIT} steps.")
        flow_event("ask_question", f"agent_{name}")
        return {"state": step(graph_state["state"]), "steps": taken + 1}

    return run


def _release(step: Callable[[AgentState], AgentOutcome]) -> Callable[[GraphState], dict]:
    return lambda graph_state: {"outcome": step(graph_state["state"])}


def _branch(
    graph: StateGraph, source: str, predicate: Predicate, chosen: str, otherwise: str
) -> None:
    def next_step(graph_state: GraphState) -> str:
        """Named for the trace, where each edge decision appears under its node."""
        return chosen if predicate(graph_state["state"]) else otherwise

    graph.add_conditional_edges(source, next_step, [chosen, otherwise])
