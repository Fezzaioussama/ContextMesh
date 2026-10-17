"""The evaluation runner: repeats, bounded concurrency, agent errors, means, thresholds."""

import threading
from uuid import uuid4

import pytest
from app.services.agent.graph import AgentWorkflow
from app.services.agent.steps import Agent
from app.services.evaluation.cases import EvalCase
from app.services.evaluation.judge import Judge
from app.services.evaluation.runner import (
    METRICS,
    Evaluator,
    RunPlan,
    failed_thresholds,
    summarized,
)
from app.services.models import CatalogSource, Identity, RetrievalResult, Usage
from app.utils.errors import ContextMeshError
from support import ScriptedReasoning, cited_outcome, evidence

IDENTITY = Identity("evaluation-test", uuid4())
CATALOG = (CatalogSource(uuid4(), "Tidepool", "Plans and support", 3),)
PLANS = evidence("tidepool-plans.md", "Harbor costs $14 per user per month.")


def case(case_id, question, tags=(), **fields):
    values = {"expected_route": "retrieve", "answerable": True, "tags": tags}
    return EvalCase(id=case_id, question=question, **{**values, **fields})


class ScriptedWorkflow:
    """Answers each question from a script; an exception in the script is raised."""

    def __init__(self, script):
        self.script = script
        self.questions = []

    def run(self, identity, question, history, catalog):
        self.questions.append(question)
        reply = self.script[question]
        if isinstance(reply, Exception):
            raise reply
        return reply


def scripted_judge(grade=3):
    model = ScriptedReasoning()
    model.script["judge_faithfulness"] = lambda content: {
        "verdicts": [{"claim_id": claim["id"], "supported": True} for claim in content["claims"]]
    }
    model.script["judge_correctness"] = lambda content: {"grade": grade}
    return Judge(model, timeout_seconds=5.0)


def evaluate(workflow, cases, judge=None, repeats=1, concurrency=2):
    plan = RunPlan(tuple(cases), IDENTITY, CATALOG, repeats, concurrency)
    return Evaluator(workflow, judge or scripted_judge()).run(plan)


def test_each_case_runs_repeats_times_and_results_keep_dataset_order():
    workflow = ScriptedWorkflow({"one": cited_outcome((PLANS,)), "two": cited_outcome((PLANS,))})
    results = evaluate(workflow, [case("a", "one"), case("b", "two")], repeats=3)
    assert [(result.case.id, result.repeat) for result in results] == [
        ("a", 1),
        ("a", 2),
        ("a", 3),
        ("b", 1),
        ("b", 2),
        ("b", 3),
    ]
    assert sorted(workflow.questions) == ["one"] * 3 + ["two"] * 3


def test_runs_share_a_bounded_pool():
    lock, active, peak = threading.Lock(), [0], [0]
    both_running = threading.Barrier(2, timeout=5)

    class Overlapping:
        def run(self, identity, question, history, catalog):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            both_running.wait()  # Fails unless two runs are in flight together.
            with lock:
                active[0] -= 1
            return cited_outcome()

    results = evaluate(Overlapping(), [case("a", "one")], repeats=4, concurrency=2)
    assert ([result.agent_error for result in results], peak[0]) == ([None] * 4, 2)


def test_agent_errors_are_recorded_and_the_evaluation_continues():
    workflow = ScriptedWorkflow(
        {
            "slow": ContextMeshError("agent_deadline_exceeded"),
            "broken": RuntimeError("private detail"),
            "fine": cited_outcome((PLANS,), (PLANS,)),
        }
    )
    results = evaluate(workflow, [case("a", "slow"), case("b", "broken"), case("c", "fine")])
    assert [(result.agent_error, result.metrics.get("completed")) for result in results] == [
        ("agent_deadline_exceeded", False),
        ("RuntimeError", False),
        (None, True),
    ]
    assert (results[0].outcome, results[0].shortfalls) == (
        None,
        ("agent error agent_deadline_exceeded",),
    )
    summary = summarized(results)
    assert (summary.agent_errors, summary.overall["completed"]) == (2, pytest.approx(1 / 3))


def test_means_cover_applicable_runs_overall_and_per_tag():
    workflow = ScriptedWorkflow(
        {
            "price": cited_outcome((PLANS,), (PLANS,)),
            "greeting": cited_outcome(status="direct", stages=("route", "direct", "release")),
        }
    )
    cases = [
        case("price", "price", ("pricing",), expected_documents=("tidepool-plans.md",)),
        case("greeting", "greeting", ("direct",), expected_route="direct"),
    ]
    summary = summarized(evaluate(workflow, cases, scripted_judge(grade=1)))
    assert summary.overall["route_correct"] == 1.0
    assert (summary.overall["document_recall"], summary.overall["faithfulness"]) == (1.0, 1.0)
    assert "correctness" not in summary.overall  # No case has a reference answer.
    assert "abstention_correct" not in summary.by_tag["direct"]  # An answerable direct case.
    assert set(summary.by_tag) == {"direct", "pricing"}
    assert "document_recall" not in summary.by_tag["direct"]
    assert list(summary.overall) == [name for name in METRICS if name in summary.overall]
    assert (summary.runs, summary.judge_errors, summary.judge_usage) == (2, 0, Usage(10, 5))


def test_judge_errors_and_low_scores_are_the_shortfalls_of_a_run():
    model = ScriptedReasoning()
    model.script["judge_faithfulness"] = lambda content: {"verdicts": "?"}
    model.script["judge_correctness"] = lambda content: {"grade": 2}
    workflow = ScriptedWorkflow({"price": cited_outcome((PLANS,), ())})
    cases = [case("p", "price", reference_answer="$14.", expected_quotes=("$14",))]
    (result,) = evaluate(workflow, cases, Judge(model, timeout_seconds=5.0))
    assert result.shortfalls == (
        "faithfulness not judged (model_output_invalid)",
        "quote_recall=0.00",
        "correctness=0.67",
    )
    assert summarized((result,)).judge_errors == 1


def test_thresholds_fail_below_the_minimum_or_when_a_metric_never_applied():
    workflow = ScriptedWorkflow({"price": cited_outcome((PLANS,), (PLANS,))})
    summary = summarized(evaluate(workflow, [case("p", "price", expected_quotes=("$15",))]))
    thresholds = {"completed": 1.0, "quote_recall": 0.5, "correctness": 0.5}
    assert failed_thresholds(summary, thresholds) == {"quote_recall": 0.0, "correctness": None}
    assert failed_thresholds(summary, {"route_correct": 1.0}) == {}


class OnePassageSearch:
    """A retriever that always finds the Harbor price."""

    def search(self, identity, source_ids, queries, round_number):
        return RetrievalResult((PLANS,), 1, True)


def test_the_real_agent_is_scored_from_the_passages_it_drafted_from(flow_lines):
    workflow = AgentWorkflow(Agent(ScriptedReasoning(), OnePassageSearch()))
    golden = case("harbor", "Harbor price?", expected_quotes=("$14 per user",))
    (result,) = evaluate(workflow, [golden])
    assert result.outcome.context == (PLANS,)
    assert (result.metrics["quote_recall"], result.metrics["faithfulness"]) == (1.0, 1.0)
    scored_line = [line for line in flow_lines if "step=case_scored" in line]
    assert scored_line == [
        "flow=evaluate_agent step=case_scored case_id=harbor repeat=1 status=answered "
        f"tokens={result.outcome.usage.total} judge_errors=0"
    ]
    assert any("flow=ask_question step=agent_plan case_id=harbor" in line for line in flow_lines)
    assert not any("Harbor price?" in line for line in flow_lines)
