"""Bounded agent behavior through the real LangGraph adapter with scripted models."""

import logging
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.services.agent.graph import RECURSION_LIMIT, AgentWorkflow, build_graph
from app.services.agent.state import AgentPolicy
from app.services.agent.steps import UNSPECIFIED_GAP, Agent
from app.services.models import (
    CatalogSource,
    ChoiceReply,
    Evidence,
    Identity,
    Locator,
    Message,
    Passage,
    RetrievalResult,
    SearchQueries,
    Usage,
)
from app.utils.errors import ContextMeshError
from app.utils.logging import flow_logger
from langgraph.errors import GraphRecursionError
from support import ScriptedReasoning

IDENTITY = Identity("agent-test", uuid4())


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class ScriptedDecision:
    def __init__(self, choice="retrieve", confidence=0.95, configured=True):
        self.configured = configured
        self.reply = ChoiceReply(
            choice,
            {"direct": confidence if choice == "direct" else 1.0 - confidence},
            confidence,
            Usage(3, 1),
        )
        self.tasks = []
        self.failure = None

    def choose(self, task):
        self.tasks.append(task)
        if self.failure is not None:
            raise self.failure
        return self.reply


class SourceRetriever:
    """Returns each selected source's passages, in catalog order."""

    def __init__(self, corpus):
        self.corpus = corpus
        self.calls = []

    def search(self, identity, source_ids, queries, round_number):
        self.calls.append((source_ids, queries))
        passages = [item for source_id in source_ids for item in self.corpus.get(source_id, [])]
        evidence = tuple(
            Evidence(item, 1.0 - index / 100, round_number) for index, item in enumerate(passages)
        )
        return RetrievalResult(evidence, len(evidence), True)


def passage(source_id, text):
    return Passage(
        uuid4(),
        source_id,
        uuid4(),
        uuid4(),
        uuid4(),
        "Source",
        "notes.md",
        Locator(("H",), 1, 2),
        text,
    )


def source(name, documents=1):
    return CatalogSource(uuid4(), name, f"{name} documents", documents)


def run(
    reasoning,
    retriever,
    catalog,
    clock=None,
    policy=AgentPolicy(),
    history=(),
    decisions=None,
    question="When does the OIDC rollout start?",
):
    agent = Agent(reasoning, retriever, decisions, policy, clock or Clock())
    return AgentWorkflow(agent).run(IDENTITY, question, history, catalog)


def plan_only(source_id):
    def plan(content):
        return {
            "standalone_question": content["question"],
            "source_ids": [str(source_id)],
            "questions": ["oidc"],
            "keywords": [],
            "hypothetical_answer": "",
        }

    return plan


def searched(retriever):
    return [call[0] for call in retriever.calls]


def cited(outcome):
    return {citation.source_id for citation in outcome.answer.citations}


def stages(outcome):
    return [stage.stage for stage in outcome.trace]


def claim_texts(outcome):
    return sorted(claim.text for claim in outcome.answer.claims)


def expanding_verdicts(target):
    verdicts = iter(
        [
            {
                "sufficient": False,
                "gaps": ["rollout date"],
                "questions": [],
                "keywords": [],
                "expand_source_ids": [str(target)],
            },
            {
                "sufficient": True,
                "gaps": [],
                "questions": [],
                "keywords": [],
                "expand_source_ids": [],
            },
        ]
    )
    return lambda content: next(verdicts)


EXPANSION_TRACE = [
    "route",
    "plan",
    "retrieve",
    "rerank",
    "expand",
    "retrieve",
    "rerank",
    "assess",
    "answer",
    "verify",
    "release",
]


def cite_all(content):
    return {
        "status": "answered",
        "claims": [
            {"text": item["text"], "evidence_ids": [item["id"]]} for item in content["passages"]
        ],
        "gaps": [],
    }


def test_missing_detail_triggers_one_expansion_to_another_source():
    decisions, rollout = source("Decisions"), source("Rollout notes")
    retriever = SourceRetriever(
        {
            decisions.id: [passage(decisions.id, "We chose OIDC.")],
            rollout.id: [passage(rollout.id, "Rollout starts in March.")],
        }
    )
    reasoning = ScriptedReasoning()
    reasoning.script.update(
        search_plan=plan_only(decisions.id),
        evidence_assessment=expanding_verdicts(rollout.id),
        grounded_answer=cite_all,
    )
    outcome = run(reasoning, retriever, (decisions, rollout))
    assert (searched(retriever), cited(outcome), stages(outcome)) == (
        [(decisions.id,), (rollout.id,)],
        {decisions.id, rollout.id},
        EXPANSION_TRACE,
    )
    assert (claim_texts(outcome), outcome.answer.status) == (
        ["Rollout starts in March.", "We chose OIDC."],
        "answered",
    )
    assert outcome.answer.text.endswith("[2]")


def test_planner_cannot_select_unknown_or_out_of_scope_sources():
    allowed, foreign = source("Allowed"), source("Foreign")
    retriever = SourceRetriever({allowed.id: [passage(allowed.id, "Allowed fact.")]})
    reasoning = ScriptedReasoning()
    reasoning.script["search_plan"] = lambda content: {
        "standalone_question": content["question"],
        "source_ids": [str(foreign.id), "not-a-uuid"],
        "questions": ["x"],
        "keywords": [],
        "hypothetical_answer": "",
    }
    reasoning.script["evidence_assessment"] = lambda content: {
        "sufficient": False,
        "gaps": [],
        "questions": [],
        "keywords": [],
        "expand_source_ids": [str(foreign.id)],
    }
    run(reasoning, retriever, (allowed,))
    offered = reasoning.contents("search_plan")[0]["sources"]
    assert (searched(retriever), offered[0]["id"], len(offered)) == (
        [(allowed.id,)],
        str(allowed.id),
        1,
    )


def test_repeated_gaps_stop_at_the_round_budget_with_a_partial_answer():
    only = source("Handbook")
    retriever = SourceRetriever({only.id: [passage(only.id, "Partial fact.")]})
    reasoning = ScriptedReasoning()
    counter = iter(range(100))
    reasoning.script["evidence_assessment"] = lambda content: {
        "sufficient": False,
        "gaps": ["missing owner"],
        "questions": [],
        "keywords": [f"variant {next(counter)}"],
        "expand_source_ids": [],
    }
    outcome = run(reasoning, retriever, (only,))
    rounds = AgentPolicy().max_rounds
    assessments = reasoning.names().count("evidence_assessment")
    assert (len(retriever.calls), assessments) == (rounds, rounds - 1)
    assert (outcome.answer.status, outcome.answer.gaps) == ("partial", ("missing owner",))


def test_unsupported_claim_gets_one_repair_and_is_then_removed():
    only = source("Handbook")
    retriever = SourceRetriever(
        {only.id: [passage(only.id, "Fact one."), passage(only.id, "Fact two.")]}
    )
    reasoning = ScriptedReasoning()
    reasoning.script["grounded_answer"] = cite_all
    reasoning.script["claim_support"] = lambda content: {
        "verdicts": [{"index": 0, "supported": True}, {"index": 1, "supported": False}]
    }
    outcome = run(reasoning, retriever, (only,))
    names = reasoning.names()
    assert (names.count("grounded_answer"), names.count("claim_support")) == (2, 2)
    assert reasoning.contents("grounded_answer")[1]["rejected_claims"] == ["Fact two."]
    removal = outcome.trace[-1].summary.endswith("Removed 1 unsupported claim.")
    assert (claim_texts(outcome), outcome.answer.status, removal) == (
        ["Fact one."],
        "partial",
        True,
    )


def test_partial_verdict_without_named_gaps_is_explained():
    only = source("Handbook")
    retriever = SourceRetriever({only.id: [passage(only.id, "Fact.")]})
    reasoning = ScriptedReasoning()
    reasoning.script["grounded_answer"] = lambda content: {
        "status": "partial",
        "claims": [{"text": "Fact.", "evidence_ids": ["E1"]}],
        "gaps": [],
    }
    outcome = run(reasoning, retriever, (only,))
    assert outcome.answer.status == "partial"
    assert outcome.answer.gaps == (UNSPECIFIED_GAP,)


def test_token_budget_is_enforced_before_each_model_call():
    only = source("Handbook")
    reasoning = ScriptedReasoning()
    tight = AgentPolicy(max_total_tokens=20)
    with pytest.raises(ContextMeshError) as failure:
        run(
            reasoning,
            SourceRetriever({only.id: [passage(only.id, "Fact.")]}),
            (only,),
            policy=tight,
        )
    assert (failure.value.code, len(reasoning.tasks)) == ("agent_budget_exceeded", 2)


def test_a_repair_without_claims_skips_the_second_verification():
    only = source("Handbook")
    retriever = SourceRetriever({only.id: [passage(only.id, "Fact.")]})
    reasoning = ScriptedReasoning()
    drafts = iter(
        [cite_all, lambda content: {"status": "insufficient_evidence", "claims": [], "gaps": []}]
    )
    reasoning.script["grounded_answer"] = lambda content: next(drafts)(content)
    reasoning.script["claim_support"] = lambda content: {"verdicts": []}
    outcome = run(reasoning, retriever, (only,))
    names = reasoning.names()
    assert (names.count("claim_support"), outcome.answer.status) == (1, "insufficient_evidence")


def test_missing_support_verdict_fails_closed():
    only = source("Handbook")
    retriever = SourceRetriever({only.id: [passage(only.id, "Fact.")]})
    reasoning = ScriptedReasoning()
    reasoning.script["claim_support"] = lambda content: {"verdicts": []}
    outcome = run(reasoning, retriever, (only,))
    assert outcome.answer.status == "insufficient_evidence"
    assert outcome.answer.citations == ()


def test_claims_citing_unknown_passages_are_dropped_before_verification():
    only = source("Handbook")
    retriever = SourceRetriever({only.id: [passage(only.id, "Fact.")]})
    reasoning = ScriptedReasoning()
    reasoning.script["grounded_answer"] = lambda content: {
        "status": "answered",
        "claims": [{"text": "Invented", "evidence_ids": ["E99"]}],
        "gaps": [],
    }
    outcome = run(reasoning, retriever, (only,))
    assert "claim_support" not in reasoning.names()
    assert outcome.answer.status == "insufficient_evidence"


def test_no_searchable_source_returns_a_gap_without_model_calls():
    reasoning = ScriptedReasoning()
    outcome = run(reasoning, SourceRetriever({}), (source("Empty", documents=0),))
    assert reasoning.tasks == []
    assert outcome.answer.status == "insufficient_evidence"
    assert outcome.answer.gaps


def test_confident_direct_route_answers_without_sources_or_retrieval():
    reasoning = ScriptedReasoning()
    reasoning.script["direct_answer"] = lambda content: {"answer": "Hello! How can I help?"}
    decisions = ScriptedDecision("direct", 0.91)
    retriever = SourceRetriever({})
    outcome = run(
        reasoning,
        retriever,
        (source("Empty", documents=0),),
        decisions=decisions,
        question="Hello!",
    )
    observed = (
        outcome.answer.status,
        outcome.answer.text,
        outcome.answer.claims,
        outcome.answer.citations,
        outcome.answer.gaps,
        retriever.calls,
        reasoning.names(),
        stages(outcome),
        outcome.usage.total,
        "current information" in decisions.tasks[0].criteria["retrieve"],
        "untrusted quoted data" in decisions.tasks[0].instructions,
    )
    assert observed == (
        "direct",
        "Hello! How can I help?",
        (),
        (),
        (),
        [],
        ["direct_answer"],
        ["route", "direct", "release"],
        19,
        True,
        True,
    )


def test_retrieval_route_without_searchable_sources_reports_an_evidence_gap():
    reasoning = ScriptedReasoning()
    outcome = run(
        reasoning,
        SourceRetriever({}),
        (),
        decisions=ScriptedDecision("retrieve"),
        question="What does our private rollout plan say?",
    )
    assert reasoning.tasks == []
    assert (outcome.answer.status, stages(outcome), outcome.usage.total) == (
        "insufficient_evidence",
        ["route", "release"],
        4,
    )


def message(role, content):
    return Message(uuid4(), uuid4(), role, content, datetime.now(UTC))


def test_route_sees_indexed_topics_and_only_recent_user_requests():
    indexed, empty = source("System design primer"), source("Drafts", documents=0)
    history = (
        message("user", "first"),
        message("assistant", "A direct reply that must not bias routing."),
        message("user", "second"),
        message("user", "third"),
        message("user", "fourth"),
    )
    decisions = ScriptedDecision("retrieve")
    run(
        ScriptedReasoning(),
        SourceRetriever({}),
        (indexed, empty),
        policy=AgentPolicy(route_history_requests=3),
        history=history,
        decisions=decisions,
        question="Explain the design patterns",
    )
    assert dict(decisions.tasks[0].state) == {
        "request": "Explain the design patterns",
        "earlier_requests": ["second", "third", "fourth"],
        "sources": [
            {"name": "System design primer", "description": "System design primer documents"}
        ],
    }


class FlowLines(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def flow_lines():
    collector = FlowLines()
    flow_logger.addHandler(collector)
    flow_logger.setLevel(logging.INFO)
    yield collector.lines
    flow_logger.removeHandler(collector)


def test_route_decision_is_traced_and_logged_without_content(flow_lines):
    reasoning = ScriptedReasoning()
    reasoning.script["direct_answer"] = lambda content: {"answer": "Hi!"}
    outcome = run(
        reasoning,
        SourceRetriever({}),
        (),
        decisions=ScriptedDecision("direct", 0.93),
        question="Hi there",
    )
    decided = "flow=ask_question step=route_decided route=direct p_direct=0.93 confidence=0.93"
    assert (outcome.trace[0].summary.endswith("(p(direct)=0.93)"), decided in flow_lines) == (
        True,
        True,
    )


@pytest.mark.parametrize(
    "decisions",
    [
        ScriptedDecision("direct", 0.79),
        ScriptedDecision("unknown", 0.99),
        ScriptedDecision(configured=False),
    ],
    ids=["low-confidence", "unknown-choice", "unavailable"],
)
def test_uncertain_or_unavailable_classifier_falls_back_to_retrieval(decisions):
    only = source("Handbook")
    retriever = SourceRetriever({only.id: [passage(only.id, "Grounded fact.")]})
    reasoning = ScriptedReasoning()
    outcome = run(reasoning, retriever, (only,), decisions=decisions)
    assert retriever.calls
    assert "search_plan" in reasoning.names()
    assert outcome.answer.status == "answered"


def test_classifier_failure_falls_back_to_retrieval():
    only = source("Handbook")
    retriever = SourceRetriever({only.id: [passage(only.id, "Grounded fact.")]})
    reasoning = ScriptedReasoning()
    decisions = ScriptedDecision()
    decisions.failure = ContextMeshError("provider_unavailable")
    outcome = run(reasoning, retriever, (only,), decisions=decisions)
    assert (len(decisions.tasks), len(retriever.calls), outcome.answer.status) == (1, 1, "answered")


def test_empty_results_expand_to_remaining_sources_without_a_model_assessment():
    first, second = source("First"), source("Second")
    retriever = SourceRetriever({second.id: [passage(second.id, "Found later.")]})
    reasoning = ScriptedReasoning()
    reasoning.script["search_plan"] = plan_only(first.id)
    outcome = run(reasoning, retriever, (first, second))
    assert [call[0] for call in retriever.calls] == [(first.id,), (second.id,)]
    assert outcome.answer.citations[0].source_id == second.id


def test_low_remaining_time_skips_further_search_but_still_verifies():
    only = source("Handbook")
    clock = Clock()
    reasoning = ScriptedReasoning()

    def slow_plan(content):
        clock.now = 35
        return plan_only(only.id)(content)

    reasoning.script["search_plan"] = slow_plan
    outcome = run(
        reasoning, SourceRetriever({only.id: [passage(only.id, "Fact.")]}), (only,), clock
    )
    names = reasoning.names()
    longest = max(task.timeout_seconds for task in reasoning.tasks[1:])
    assert ("evidence_assessment" in names, names[-1], longest <= 25) == (
        False,
        "claim_support",
        True,
    )
    assert outcome.answer.status == "answered"


def test_exhausted_deadline_is_an_explicit_failure():
    only = source("Handbook")
    clock = Clock()
    reasoning = ScriptedReasoning()

    def stalled_plan(content):
        clock.now = 59.5
        return plan_only(only.id)(content)

    reasoning.script["search_plan"] = stalled_plan
    with pytest.raises(ContextMeshError) as failure:
        run(reasoning, SourceRetriever({only.id: [passage(only.id, "Fact.")]}), (only,), clock)
    assert failure.value.code == "agent_deadline_exceeded"


def test_malformed_structured_output_is_a_safe_model_failure():
    only = source("Handbook")
    reasoning = ScriptedReasoning()
    reasoning.script["evidence_assessment"] = lambda content: {
        "sufficient": "yes",
        "gaps": [],
        "questions": [],
        "keywords": [],
        "expand_source_ids": [],
    }
    with pytest.raises(ContextMeshError) as failure:
        run(reasoning, SourceRetriever({only.id: [passage(only.id, "Fact.")]}), (only,))
    assert failure.value.code == "model_output_invalid"


def test_answer_generation_sees_passages_but_not_conversation_history():
    only = source("Handbook")
    reasoning = ScriptedReasoning()
    run(reasoning, SourceRetriever({only.id: [passage(only.id, "Fact.")]}), (only,))
    assert "conversation" in reasoning.contents("search_plan")[0]
    assert set(reasoning.contents("grounded_answer")[0]) == {"question", "passages"}


def test_plan_searches_the_standalone_question_first_with_typed_rewrites():
    only = source("Handbook")
    retriever = SourceRetriever({only.id: [passage(only.id, "Fact.")]})
    reasoning = ScriptedReasoning()
    reasoning.script["search_plan"] = lambda content: {
        "standalone_question": "When does the OIDC rollout start?",
        "source_ids": [],
        "questions": [
            "when does the  OIDC rollout start?",
            "OIDC launch date",
            "SSO rollout schedule",
            "Who owns the OIDC rollout?",
            "one too many",
        ],
        "keywords": ["OIDC rollout", "SSO", "one too many"],
        "hypothetical_answer": "The OIDC rollout starts in March.",
    }
    outcome = run(reasoning, retriever, (only,))
    assert retriever.calls[0][1] == SearchQueries(
        (
            "When does the OIDC rollout start?",
            "OIDC launch date",
            "SSO rollout schedule",
            "Who owns the OIDC rollout?",
        ),
        ("OIDC rollout", "SSO"),
        "The OIDC rollout starts in March.",
    )
    assert outcome.trace[1].summary.endswith(
        "4 questions, 2 keyword queries and a hypothetical answer."
    )


def graded_passages():
    only = source("Handbook")
    texts = ("Lunch is at noon.", "We chose OIDC.", "Rollout starts in March.")
    retriever = SourceRetriever({only.id: [passage(only.id, text) for text in texts]})
    reasoning = ScriptedReasoning()
    reasoning.script["grounded_answer"] = cite_all
    return only, retriever, reasoning


def answered_from(reasoning):
    return [item["text"] for item in reasoning.contents("grounded_answer")[0]["passages"]]


def test_grading_drops_irrelevant_passages_and_reorders_the_rest():
    only, retriever, reasoning = graded_passages()
    reasoning.script["passage_rerank"] = lambda content: {
        "grades": [
            {"id": "E1", "relevance": 0},
            {"id": "E2", "relevance": 2},
            {"id": "E3", "relevance": 3},
        ]
    }
    outcome = run(reasoning, retriever, (only,))
    graded = next(stage for stage in outcome.trace if stage.stage == "rerank")
    assert answered_from(reasoning) == ["Rollout starts in March.", "We chose OIDC."]
    assert graded.summary == "Graded 3 passages: kept 2, dropped 1 as irrelevant."


def test_grading_passages_sends_truncated_text():
    only, retriever, reasoning = graded_passages()
    run(reasoning, retriever, (only,), policy=AgentPolicy(rerank_text_chars=5))
    sent = reasoning.contents("passage_rerank")[0]["passages"]
    assert [item["text"] for item in sent] == ["Lunch", "We ch", "Rollo"]


def unavailable_provider(content):
    raise ContextMeshError("provider_unavailable")


@pytest.mark.parametrize(
    "grader",
    [lambda content: {"grades": "all good"}, unavailable_provider],
    ids=["malformed", "provider-failure"],
)
def test_failed_grading_keeps_the_retrieval_order(grader):
    only, retriever, reasoning = graded_passages()
    reasoning.script["passage_rerank"] = grader
    outcome = run(reasoning, retriever, (only,))
    graded = next(stage for stage in outcome.trace if stage.stage == "rerank")
    assert answered_from(reasoning) == [
        "Lunch is at noon.",
        "We chose OIDC.",
        "Rollout starts in March.",
    ]
    assert graded.summary == "Passage grading failed safely; kept the retrieval order."


def test_grading_can_be_turned_off():
    only, retriever, reasoning = graded_passages()
    outcome = run(reasoning, retriever, (only,), policy=AgentPolicy(rerank=False))
    assert "passage_rerank" not in reasoning.names()
    assert (len(answered_from(reasoning)), outcome.answer.status) == (3, "answered")


def test_grading_never_hides_a_budget_failure():
    only, retriever, reasoning = graded_passages()
    with pytest.raises(ContextMeshError) as failure:
        run(reasoning, retriever, (only,), policy=AgentPolicy(max_total_tokens=15))
    assert (failure.value.code, reasoning.names()) == ("agent_budget_exceeded", ["search_plan"])


class EndlessSearch:
    """An agent that always asks for another search, to exercise the graph-level bound."""

    def __init__(self):
        self.executed = 0

    def step(self, state):
        self.executed += 1
        return state

    def __getattr__(self, name):
        predicates = {"should_answer_direct": False, "has_sources": True, "should_search": True}
        if name in predicates:
            return lambda state: predicates[name]
        return self.step


def test_the_whole_run_is_bounded_across_subgraphs():
    agent = EndlessSearch()
    with pytest.raises(GraphRecursionError):
        build_graph(agent).invoke({"state": object()})
    assert agent.executed == RECURSION_LIMIT - 1
