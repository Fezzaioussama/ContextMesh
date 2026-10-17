"""Runs golden cases through the agent, scores every run, and aggregates the scores.

Each case runs `repeats` times on a bounded thread pool, because the agent is synchronous.
A failed agent run is recorded with its error code and the evaluation goes on; its
`completed` metric is 0, so failures lower the means instead of disappearing from them.
"""

import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from statistics import fmean

from app.services.agent.graph import AgentWorkflow
from app.services.evaluation.cases import (
    BUDGET_METRICS,
    DETERMINISTIC_QUALITY,
    EvalCase,
    scored,
)
from app.services.evaluation.judge import JUDGE_METRICS, Judge
from app.services.models import AgentOutcome, CatalogSource, Identity, Usage
from app.utils.errors import failure_code
from app.utils.logging import flow_event, flow_scope

FLOW = "evaluate_agent"
# `completed` is 0 for a failed run, so failures lower the means of quality metrics.
QUALITY_METRICS = ("completed", *DETERMINISTIC_QUALITY, *JUDGE_METRICS)
METRICS = (*QUALITY_METRICS, *BUDGET_METRICS)


@dataclass(frozen=True)
class RunPlan:
    """What to evaluate, as whom, over which sources, how often, and how many at once."""

    cases: tuple[EvalCase, ...]
    identity: Identity
    catalog: tuple[CatalogSource, ...]
    repeats: int = 1
    concurrency: int = 4


@dataclass(frozen=True)
class CaseResult:
    """One run of one case.

    Attributes:
        repeat: The run's number, from 1 to the plan's `repeats`.
        outcome: The agent's outcome; None when the run failed with `agent_error`.
        metrics: Every metric by name; None where it did not apply.
        judge_errors: The error code of each judge metric that could not be scored.
    """

    case: EvalCase
    repeat: int
    outcome: AgentOutcome | None
    metrics: Mapping[str, float | None]
    agent_error: str | None = None
    judge_errors: Mapping[str, str] = field(default_factory=dict)
    judge_usage: Usage = Usage(0, 0)

    @property
    def shortfalls(self) -> tuple[str, ...]:
        """Why this run is not perfect; empty for a perfect run.

        A failed run reports its agent error alone; any other run reports its judge errors
        and every quality score below 1.
        """
        if self.agent_error is not None:
            return (f"agent error {self.agent_error}",)
        judged = tuple(
            f"{metric} not judged ({code})" for metric, code in self.judge_errors.items()
        )
        scores = ((name, self.metrics.get(name)) for name in QUALITY_METRICS)
        low = tuple(
            f"{name}={value:.2f}" for name, value in scores if value is not None and value < 1
        )
        return judged + low


@dataclass(frozen=True)
class Summary:
    """Each metric's mean over the runs where it applied, overall and per case tag."""

    overall: Mapping[str, float]
    by_tag: Mapping[str, Mapping[str, float]]
    runs: int
    agent_errors: int
    judge_errors: int
    judge_usage: Usage


class Evaluator:
    """Runs a plan's cases through the agent and scores every run."""

    def __init__(
        self,
        workflow: AgentWorkflow,
        judge: Judge,
        clock: Callable[[], float] = time.perf_counter,
    ):
        self._workflow = workflow
        self._judge = judge
        self._clock = clock

    def run(self, plan: RunPlan) -> tuple[CaseResult, ...]:
        """Run every case `repeats` times, at most `concurrency` at once, in dataset order."""
        runs = [(case, repeat) for case in plan.cases for repeat in range(1, plan.repeats + 1)]
        with ThreadPoolExecutor(max_workers=plan.concurrency) as pool:
            return tuple(pool.map(lambda run: self._evaluated(plan, *run), runs))

    def _evaluated(self, plan: RunPlan, case: EvalCase, repeat: int) -> CaseResult:
        """One scored run; an agent failure is recorded with its code instead of raised.

        Latency covers the agent alone, not the judge.
        """
        with flow_scope(case_id=case.id, repeat=repeat):
            started = self._clock()
            try:
                outcome = self._workflow.run(
                    plan.identity, case.question, case.messages(), plan.catalog
                )
            except Exception as error:  # One failed run must not stop the evaluation.
                code = failure_code(error)
                flow_event(FLOW, "case_failed", code=code)
                return CaseResult(case, repeat, None, {"completed": False}, code)
            metrics = scored(case, outcome, self._clock() - started)
            judgement = self._judge.judged(case, outcome)
            flow_event(
                FLOW,
                "case_scored",
                status=outcome.answer.status,
                tokens=outcome.usage.total,
                judge_errors=len(judgement.errors),
            )
            scores = {"completed": True, **metrics, **judgement.scores}
            return CaseResult(
                case, repeat, outcome, scores, None, judgement.errors, judgement.usage
            )


def summarized(results: tuple[CaseResult, ...]) -> Summary:
    """Aggregate the runs: means overall and per tag, error counts, and judge tokens."""
    tags = sorted({tag for result in results for tag in result.case.tags})
    return Summary(
        means(results),
        {tag: means(tuple(item for item in results if tag in item.case.tags)) for tag in tags},
        len(results),
        sum(result.agent_error is not None for result in results),
        sum(len(result.judge_errors) for result in results),
        sum((result.judge_usage for result in results), Usage(0, 0)),
    )


def means(results: tuple[CaseResult, ...]) -> dict[str, float]:
    """Each metric's mean over the runs where it applied, in `METRICS` order.

    A metric that applied to no run is left out rather than reported as 0.
    """
    collected = {name: [result.metrics.get(name) for result in results] for name in METRICS}
    applied = {
        name: [value for value in values if value is not None] for name, values in collected.items()
    }
    return {name: fmean(values) for name, values in applied.items() if values}


def failed_thresholds(summary: Summary, thresholds: Mapping[str, float]) -> dict[str, float | None]:
    """The thresholds whose overall mean is below the minimum, mapped to that mean.

    A metric that applied to no run fails with None: nothing showed that it holds.
    """
    observed = {name: summary.overall.get(name) for name in thresholds}
    return {
        name: mean for name, mean in observed.items() if mean is None or mean < thresholds[name]
    }
