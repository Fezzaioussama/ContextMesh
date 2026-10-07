"""Bounded agent behavior through the real LangGraph adapter with scripted models."""

from uuid import uuid4

import pytest
from app.services.agent.assessor import EvidenceAssessor
from app.services.agent.budget import BudgetedModel
from app.services.agent.checker import SupportChecker
from app.services.agent.gather import EvidenceGatherer
from app.services.agent.graph import LangGraphWorkflow
from app.services.agent.planner import SearchPlanner
from app.services.agent.policy import AgentPolicy
from app.services.agent.steps import AgentSteps
from app.services.agent.writer import AnswerWriter
from app.services.ports.retrieval import RetrievalResult
from app.services.rules.answers import UNSPECIFIED_GAP, Locator
from app.services.rules.knowledge import CatalogSource, Evidence, Passage
from app.utils.exceptions import ContextMeshError
from app.utils.security import Identity
from support import ScriptedReasoning

IDENTITY = Identity("agent-test", uuid4())


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


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


def run(reasoning, retriever, catalog, clock=None, policy=AgentPolicy(), history=()):
    clock = clock or Clock()
    model = BudgetedModel(reasoning, policy, clock)
    steps = AgentSteps(
        model,
        SearchPlanner(model, policy),
        EvidenceGatherer(retriever, model, policy),
        EvidenceAssessor(model, policy),
        AnswerWriter(model, policy),
        SupportChecker(model),
        policy,
    )
    return LangGraphWorkflow(steps).run(
        IDENTITY, "When does the OIDC rollout start?", history, catalog
    )


def plan_only(source_id):
    def plan(content):
        return {
            "standalone_question": content["question"],
            "source_ids": [str(source_id)],
            "queries": ["oidc"],
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
                "queries": [],
                "expand_source_ids": [str(target)],
            },
            {"sufficient": True, "gaps": [], "queries": [], "expand_source_ids": []},
        ]
    )
    return lambda content: next(verdicts)


EXPANSION_TRACE = [
    "plan",
    "retrieve",
    "expand",
    "retrieve",
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
        "queries": ["x"],
    }
    reasoning.script["evidence_assessment"] = lambda content: {
        "sufficient": False,
        "gaps": [],
        "queries": [],
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
        "queries": [f"variant {next(counter)}"],
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
        "queries": [],
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
