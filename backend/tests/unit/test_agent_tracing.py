"""LangSmith tracing of a real LangGraph turn, recorded by a client that never sends."""

from uuid import uuid4

import pytest
from app.data.tracing import (
    DisabledTracing,
    LangSmithTracing,
    TracedEmbeddingModel,
    TracedReasoningModel,
    TracedSearch,
)
from app.services.agent.graph import AgentWorkflow
from app.services.agent.steps import Agent
from app.services.models import CatalogSource, Evidence, Identity, Locator, Passage, RetrievalResult
from app.utils.config import Settings
from app.utils.errors import ContextMeshError
from app.utils.logging import flow_scope
from app.wiring import ExternalServices, create_tracing, traced_search, traced_services
from langsmith import Client
from langsmith.run_helpers import get_current_run_tree
from pydantic import ValidationError
from support import HashEmbeddings, ScriptedReasoning

IDENTITY = Identity("trace", uuid4())
SOURCE = CatalogSource(uuid4(), "Rollout notes", "Rollout documents", 1)
PASSAGE = Passage(
    uuid4(),
    SOURCE.id,
    uuid4(),
    uuid4(),
    uuid4(),
    "Rollout notes",
    "rollout.md",
    Locator(("Plan",), 1, 2),
    "The OIDC rollout starts in March.",
)


class RecordingClient(Client):
    """Keeps every run it would upload, merging the start and end of each run by id."""

    def __init__(self):
        super().__init__(api_key="test", api_url="http://127.0.0.1:9", auto_batch_tracing=False)
        self.recorded: dict[object, dict] = {}

    def create_run(self, name, inputs, run_type, **values):
        run = self.recorded.setdefault(str(values.get("id")), {})
        run.update(values, name=name, inputs=inputs, run_type=run_type)

    def update_run(self, run_id, **values):
        self.recorded.setdefault(str(run_id), {}).update(values)

    def named(self, name):
        return [run for run in self.recorded.values() if run["name"] == name]

    def roots(self):
        return [run for run in self.recorded.values() if run.get("parent_run_id") is None]

    def projects(self):
        return {run["session_name"] for run in self.recorded.values()}

    def phases(self, *names):
        """The phase of the first run with each name."""
        return {name: self.phase(self.named(name)[0]) for name in names}

    def parent_name(self, run):
        return self.lineage(run)[0]

    def lineage(self, run):
        """Names of the run's ancestors, nearest first."""
        parent = self.recorded.get(str(run.get("parent_run_id")))
        return [] if parent is None else [parent["name"], *self.lineage(parent)]

    def phase(self, run):
        return next(name for name in self.lineage(run) if name in PHASES)


PHASES = ("routing", "research", "answering")


class OneSourceSearch:
    def search(self, identity, source_ids, queries, round_number):
        return RetrievalResult((Evidence(PASSAGE, 0.9, round_number),), 1, True)


@pytest.fixture(autouse=True)
def no_environment_tracing(monkeypatch):
    for name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def workflow(reasoning, tracing=None):
    model = TracedReasoningModel(
        reasoning, provider="openrouter", model="deepseek/deepseek-v4.1-flash"
    )
    agent = Agent(model, TracedSearch(OneSourceSearch()), clock=lambda: 0.0)
    return AgentWorkflow(agent, tracing)


def traced_turn(client, reasoning=None, turn_id="turn-1"):
    tracing = LangSmithTracing(api_key="", project="contextmesh-test", endpoint="", client=client)
    with flow_scope(conversation_id="conversation-1", turn_id=turn_id):
        return workflow(reasoning or ScriptedReasoning(), tracing).run(
            IDENTITY, "When does the OIDC rollout start?", (), (SOURCE,)
        )


def test_one_turn_is_one_root_run_tagged_with_its_flow_ids():
    client = RecordingClient()
    traced_turn(client)
    (root,) = client.roots()
    assert (root["name"], client.projects()) == ("ask_question", {"contextmesh-test"})
    metadata = root["extra"]["metadata"]
    assert (metadata["conversation_id"], metadata["turn_id"], metadata["workspace_id"]) == (
        "conversation-1",
        "turn-1",
        str(IDENTITY.workspace_id),
    )


def test_each_phase_groups_its_nodes_and_model_calls():
    client = RecordingClient()
    traced_turn(client)
    assert client.phases("route", "plan", "retrieve", "assess", "verify") == {
        "route": "routing",
        "plan": "research",
        "retrieve": "research",
        "assess": "research",
        "verify": "answering",
    }
    (plan_call,) = client.named("search_plan")
    assert (plan_call["run_type"], client.parent_name(plan_call)) == ("llm", "plan")
    assert plan_call["outputs"]["usage_metadata"] == {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
    }


def test_retrieval_reports_the_hydrated_passages_as_documents():
    client = RecordingClient()
    traced_turn(client)
    (search,) = client.named("hybrid_search")
    (document,) = search["outputs"]["documents"]
    assert (search["run_type"], client.parent_name(search)) == ("retriever", "retrieve")
    assert (document["page_content"], document["metadata"]["chunk_id"]) == (
        PASSAGE.text,
        str(PASSAGE.chunk_id),
    )


def test_a_failed_model_call_is_traced_and_raises_the_same_error():
    client, reasoning = RecordingClient(), ScriptedReasoning()
    reasoning.failure = ContextMeshError("provider_unavailable")
    with pytest.raises(ContextMeshError) as raised:
        traced_turn(client, reasoning)
    assert raised.value.code == "provider_unavailable"
    (plan_call,) = client.named("search_plan")
    assert plan_call["error"]


def test_tracing_does_not_change_the_answer():
    traced = traced_turn(RecordingClient())
    plain = workflow(ScriptedReasoning()).run(
        IDENTITY, "When does the OIDC rollout start?", (), (SOURCE,)
    )
    assert (traced.answer, traced.trace, traced.usage) == (plain.answer, plain.trace, plain.usage)


def test_without_a_tracing_scope_traced_ports_upload_nothing(monkeypatch):
    uploads = []
    monkeypatch.setattr(Client, "create_run", lambda self, *args, **kwargs: uploads.append(args))
    workflow(ScriptedReasoning()).run(IDENTITY, "When does the OIDC rollout start?", (), (SOURCE,))
    TracedEmbeddingModel(HashEmbeddings()).embed(["outside any run"], "search_query")
    assert uploads == []


def test_tracing_is_off_by_default_and_requires_a_key_when_enabled(monkeypatch):
    assert Settings(_env_file=None).selected_tracing.enabled is False
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    with pytest.raises(ValidationError, match="LANGSMITH_API_KEY"):
        Settings(_env_file=None)
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    selected = Settings(_env_file=None).selected_tracing
    assert (selected.enabled, selected.project) == (True, "contextmesh")


class RunTreeProbe(ScriptedReasoning):
    """Records whether each model call ran inside a LangSmith run."""

    def __init__(self):
        super().__init__()
        self.traced = []

    def complete(self, task):
        self.traced.append(get_current_run_tree() is not None)
        return super().complete(task)


def test_disabled_tracing_overrides_legacy_tracing_variables(monkeypatch):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "http://127.0.0.1:9")
    reasoning = RunTreeProbe()
    workflow(reasoning, DisabledTracing()).run(
        IDENTITY, "When does the OIDC rollout start?", (), (SOURCE,)
    )
    assert reasoning.traced and not any(reasoning.traced)


def tracing_settings(enabled):
    return Settings(_env_file=None, langsmith_tracing=enabled, langsmith_api_key="ls-key")


class Resources:
    """Stands in for the runtime's ExitStack; records the cleanups it is given."""

    def __init__(self):
        self.cleanups = []

    def callback(self, cleanup):
        self.cleanups.append(cleanup)


def services():
    return ExternalServices(ScriptedReasoning(), HashEmbeddings(), None, None)


def test_tracing_off_keeps_every_port_and_registers_no_client():
    config, original, search = tracing_settings(False), services(), OneSourceSearch()
    resources = Resources()
    tracing = create_tracing(config, resources)
    assert (
        type(tracing),
        resources.cleanups,
        traced_services(original, config),
        traced_search(search, config),
    ) == (DisabledTracing, [], original, search)


def test_tracing_on_wraps_the_agent_ports_and_flushes_on_close(monkeypatch):
    flushed = []
    monkeypatch.setattr(Client, "flush", lambda self: flushed.append(True))
    config, resources = tracing_settings(True), Resources()
    tracing = create_tracing(config, resources)
    (close,) = resources.cleanups
    close()
    wrapped = traced_services(services(), config)
    assert (
        type(tracing),
        type(wrapped.reasoning),
        type(wrapped.embeddings),
        type(traced_search(OneSourceSearch(), config)),
        flushed,
    ) == (
        LangSmithTracing,
        TracedReasoningModel,
        TracedEmbeddingModel,
        TracedSearch,
        [True],
    )
