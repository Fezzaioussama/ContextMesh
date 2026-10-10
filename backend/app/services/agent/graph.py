"""The agent as a LangGraph run: one parent graph over three subgraphs, so a trace
groups each phase:

    routing    route → direct?
    research   plan → retrieve ⟲ assess
    answering  generate → verify ⟲ repair
    release    always ends the run

The graph only sequences the steps in `steps.py`; limits and decisions live there.
LangGraph applies `recursion_limit` to each subgraph separately, so the whole-run bound
is a step counter carried in the graph state across all three subgraphs.
"""

from collections.abc import Callable
from contextlib import nullcontext
from typing import NotRequired, TypedDict

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph

from app.data.tracing import DisabledTracing, LangSmithTracing
from app.services.agent.state import AgentState
from app.services.agent.steps import Agent
from app.services.models import AgentOutcome, CatalogSource, Identity, Message
from app.utils.logging import flow_event, flow_ids

RECURSION_LIMIT = 30  # Node executions per run, release included.
RUN_NAME = "ask_question"

Step = Callable[[AgentState], AgentState]


class GraphState(TypedDict):
    state: AgentState
    outcome: NotRequired[AgentOutcome]
    steps: NotRequired[int]


class AgentWorkflow:
    def __init__(self, agent: Agent, tracing: LangSmithTracing | DisabledTracing | None = None):
        self._agent = agent
        self._tracing = tracing
        self._graph = build_graph(agent)

    def run(
        self,
        identity: Identity,
        question: str,
        history: tuple[Message, ...],
        catalog: tuple[CatalogSource, ...],
    ) -> AgentOutcome:
        initial = self._agent.start(identity, question, history, catalog)
        config = {"recursion_limit": RECURSION_LIMIT, "run_name": RUN_NAME}
        scope = nullcontext()
        if self._tracing is not None:
            ids = {**flow_ids(), "workspace_id": str(identity.workspace_id)}
            scope = self._tracing.scope(RUN_NAME, ids)
        with scope:
            result = self._graph.invoke({"state": initial}, config=config)
        return result["outcome"]


def build_graph(agent: Agent):
    routing = _phase(("route", agent.route), ("direct", agent.answer_direct))
    _branch(routing, "route", agent.should_answer_direct, "direct", END)
    routing.add_edge("direct", END)

    research = _phase(("plan", agent.plan), ("retrieve", agent.retrieve), ("assess", agent.assess))
    research.add_edge("plan", "retrieve")
    research.add_edge("retrieve", "assess")
    _branch(research, "assess", agent.should_search, "retrieve", END)

    answering = _phase(
        ("generate", agent.generate),
        ("verify", agent.verify),
        ("repair", agent.repair),
    )
    _branch(answering, "generate", agent.has_claims, "verify", END)
    _branch(answering, "verify", agent.should_repair, "repair", END)
    _branch(answering, "repair", agent.has_claims, "verify", END)

    def after_routing(graph_state: GraphState) -> str:
        state = graph_state["state"]
        if agent.should_answer_direct(state) or not agent.has_sources(state):
            return "release"
        return "research"

    graph = StateGraph(GraphState)
    graph.add_node("routing", routing.compile(name="routing"))
    graph.add_node("research", research.compile(name="research"))
    graph.add_node("answering", answering.compile(name="answering"))
    graph.add_node("release", lambda graph_state: {"outcome": agent.release(graph_state["state"])})
    graph.add_edge(START, "routing")
    graph.add_conditional_edges("routing", after_routing, ["research", "release"])
    graph.add_edge("research", "answering")
    graph.add_edge("answering", "release")
    graph.add_edge("release", END)
    return graph.compile(name=RUN_NAME)


def _phase(*nodes: tuple[str, Step]) -> StateGraph:
    """A subgraph whose first node is its entry; each node logs its flow step."""
    graph = StateGraph(GraphState)
    for node, step in nodes:
        graph.add_node(node, _counted(node, step))
    graph.add_edge(START, nodes[0][0])
    return graph


def _counted(name: str, step: Step) -> Callable[[GraphState], dict]:
    def run(graph_state: GraphState) -> dict:
        taken = graph_state.get("steps", 0)
        if taken >= RECURSION_LIMIT - 1:  # Leave the last step for release.
            raise GraphRecursionError(f"Agent run exceeded {RECURSION_LIMIT} steps.")
        flow_event("ask_question", f"agent_{name}")
        return {"state": step(graph_state["state"]), "steps": taken + 1}

    return run


def _branch(
    graph: StateGraph,
    source: str,
    condition: Callable[[AgentState], bool],
    chosen: str,
    otherwise: str,
) -> None:
    def next_step(graph_state: GraphState) -> str:
        """Named for the trace, where each edge decision appears under its node."""
        return chosen if condition(graph_state["state"]) else otherwise

    graph.add_conditional_edges(source, next_step, [chosen, otherwise])
