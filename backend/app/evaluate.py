"""The offline agent evaluation: `python -m app.evaluate --dataset PATH`.

Runs the API's agent as the development identity over a JSONL golden dataset, scores every
run with deterministic metrics and an LLM judge, and writes `report.json` and `report.md`.
With `--langsmith` it also syncs the dataset to LangSmith and logs the run as an
experiment; without it, nothing is sent to LangSmith.

Exit codes: 0 when every `--fail-under` threshold holds, 1 when one fails, 2 when the
dataset, options, configuration, or catalog cannot be evaluated, and 3 when the evaluation
fails for another reason, such as LangSmith rejecting the upload after the report was
written. The message of a code-3 failure names only its code, never its details.
"""

import json
import sys
from argparse import ArgumentParser, ArgumentTypeError, Namespace
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.data.experiments import Experiment, LangSmithExperiments
from app.data.retrieval import RetrievalRepository
from app.services.evaluation.cases import EvalCase, InvalidDataset, load_cases
from app.services.evaluation.judge import Judge
from app.services.evaluation.runner import (
    FLOW,
    QUALITY_METRICS,
    CaseResult,
    Evaluator,
    RunPlan,
    Summary,
    failed_thresholds,
    summarized,
)
from app.services.models import AgentOutcome, CatalogSource, Identity
from app.utils.config import BACKEND, Settings
from app.utils.errors import ContextMeshError, failure_code
from app.utils.logging import flow_event, show_flows
from app.wiring import build_evaluation, create_experiments

EXIT_OK = 0
EXIT_THRESHOLD_FAILED = 1
EXIT_INVALID = 2
EXIT_FAILED = 3
REPORTS = BACKEND / "evals" / "reports"
DESCRIPTION = "Evaluate the agent over a JSONL golden dataset and write a report."
SAME_JUDGE_WARNING = (
    "warning: the judge uses the agent's model (set OPENROUTER_JUDGE_MODEL to another one); "
    "a model grading its own answers is biased."
)


class InvalidSetup(Exception):
    """The options, configuration, or catalog cannot be evaluated."""


@dataclass(frozen=True)
class Report:
    """A finished evaluation, as `report.json`, `report.md`, and a LangSmith experiment."""

    dataset: Path
    started_at: datetime
    models: Mapping[str, str]
    plan: RunPlan
    results: tuple[CaseResult, ...]
    summary: Summary
    thresholds: Mapping[str, float]
    failed: Mapping[str, float | None]


def main(arguments: Sequence[str] | None = None, settings: Settings | None = None) -> int:
    """Run the evaluation and return the exit code; tests pass arguments and settings."""
    options = parser().parse_args(arguments)
    show_flows()
    try:
        config = settings or loaded_settings()
        cases = selected_cases(options.dataset, options.tag)
        checked_setup(config, options)
        with ExitStack() as resources:
            return evaluated(config, options, cases, resources)
    except (InvalidDataset, InvalidSetup) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_INVALID
    except Exception as error:  # Kept apart from exit 1, so CI never reads it as a threshold.
        code = failure_code(error)
        flow_event(FLOW, "failed", code=code)
        print(f"error: the evaluation failed ({code})", file=sys.stderr)
        return EXIT_FAILED


def parser() -> ArgumentParser:
    """The command line; argparse rejects an invalid option with exit code 2."""
    command = ArgumentParser(prog="python -m app.evaluate", description=DESCRIPTION)
    command.add_argument("--dataset", type=Path, required=True, help="JSONL golden dataset")
    command.add_argument("--repeats", type=positive, default=1, help="runs per case")
    command.add_argument("--concurrency", type=positive, default=4, help="runs at once")
    command.add_argument(
        "--tag", action="append", default=[], help="only cases with this tag (repeatable)"
    )
    command.add_argument(
        "--source",
        action="append",
        default=[],
        help="search only this source, by name (repeatable; default: every searchable one)",
    )
    command.add_argument(
        "--fail-under",
        action="append",
        default=[],
        type=threshold,
        metavar="METRIC=VALUE",
        help=f"exit 1 when the metric's mean is below VALUE; one of {', '.join(QUALITY_METRICS)}",
    )
    command.add_argument(
        "--output", type=Path, help="report directory (default: backend/evals/reports/<UTC time>)"
    )
    command.add_argument(
        "--langsmith", action="store_true", help="also log the run as a LangSmith experiment"
    )
    return command


def positive(text: str) -> int:
    """A count of at least 1."""
    value = int(text)
    if value < 1:
        raise ArgumentTypeError("must be at least 1")
    return value


def threshold(text: str) -> tuple[str, float]:
    """One `--fail-under METRIC=VALUE`: a quality metric and a minimum from 0 to 1."""
    metric, _, value = text.partition("=")
    try:
        minimum = float(value)
    except ValueError:
        raise ArgumentTypeError(f"{value!r} is not a number") from None
    if metric not in QUALITY_METRICS or not 0 <= minimum <= 1:
        metrics = ", ".join(QUALITY_METRICS)
        raise ArgumentTypeError(f"use METRIC=VALUE, VALUE from 0 to 1, METRIC one of {metrics}")
    return metric, minimum


def loaded_settings() -> Settings:
    """The settings from the environment and `.env`.

    Only here does a pydantic error mean invalid configuration; one raised later, such as
    from LangSmith's response models, is an evaluation failure (exit 3).

    Raises:
        InvalidSetup: A setting is invalid. The message names each field and problem but
            never its value, which may be a secret.
    """
    try:
        return Settings()
    except ValidationError as error:
        problems = (
            f"{'.'.join(map(str, item['loc'])) or 'settings'}: {item['msg']}"
            for item in error.errors(include_input=False, include_url=False)
        )
        raise InvalidSetup(f"invalid settings: {'; '.join(problems)}") from None


def selected_cases(dataset: Path, tags: list[str]) -> tuple[EvalCase, ...]:
    """The dataset's cases, keeping only those with one of `tags` when any is given.

    Raises:
        InvalidDataset: The dataset is not UTF-8, has an invalid or repeated case, or has
            no case.
        InvalidSetup: The file cannot be read, or no case has one of the tags.
    """
    try:
        cases = load_cases(dataset)
    except OSError as error:
        raise InvalidSetup(f"cannot read {dataset}: {error.strerror}") from None
    chosen = tuple(case for case in cases if not tags or set(tags) & set(case.tags))
    if not chosen:
        raise InvalidSetup(f"no case in {dataset} has a tag among: {', '.join(tags)}")
    return chosen


def checked_setup(config: Settings, options: Namespace) -> None:
    """Refuse to start without the credentials the run needs; warn about a biased judge.

    Raises:
        InvalidSetup: OPENROUTER_API_KEY is missing, or `--langsmith` lacks LANGSMITH_API_KEY.
    """
    if not config.openrouter_api_key.get_secret_value().strip():
        raise InvalidSetup("set OPENROUTER_API_KEY: the agent and the judge call OpenRouter")
    if options.langsmith and not config.langsmith_api_key.get_secret_value().strip():
        raise InvalidSetup("--langsmith needs LANGSMITH_API_KEY")
    if config.judge_model == config.openrouter_model:
        print(SAME_JUDGE_WARNING, file=sys.stderr)


def evaluated(
    config: Settings, options: Namespace, cases: tuple[EvalCase, ...], resources: ExitStack
) -> int:
    """Run the cases, write the report, and publish it to LangSmith when asked.

    Returns:
        EXIT_THRESHOLD_FAILED when a threshold fails, otherwise EXIT_OK.
    """
    started_at = datetime.now(UTC)
    # Without --langsmith nothing reaches LangSmith, not even LANGSMITH_TRACING's turn traces.
    untraced = config.model_copy(update={"langsmith_tracing": False})
    evaluation = build_evaluation(config if options.langsmith else untraced, resources)
    identity = Identity(config.dev_subject, config.dev_workspace_id)
    catalog = searchable_sources(evaluation.assistant.catalog, identity, options.source)
    plan = RunPlan(cases, identity, catalog, options.repeats, options.concurrency)
    flow_event(FLOW, "started", cases=len(cases), repeats=plan.repeats, sources=len(catalog))
    judge = Judge(evaluation.judge, config.provider_timeout_seconds)
    results = Evaluator(evaluation.assistant.workflow, judge).run(plan)
    summary = summarized(results)
    thresholds = dict(options.fail_under)
    failed = failed_thresholds(summary, thresholds)
    models = {"agent": config.openrouter_model, "judge": config.judge_model}
    report = Report(options.dataset, started_at, models, plan, results, summary, thresholds, failed)
    output = written(report, options.output or REPORTS / f"{started_at:%Y%m%dT%H%M%SZ}")
    print(f"Report: {output / 'report.md'}")
    if options.langsmith:
        print(f"LangSmith experiment: {published(create_experiments(config, resources), report)}")
    flow_event(
        FLOW,
        "finished",
        runs=summary.runs,
        agent_errors=summary.agent_errors,
        judge_errors=summary.judge_errors,
        failed_thresholds=len(failed),
    )
    return EXIT_THRESHOLD_FAILED if failed else EXIT_OK


def searchable_sources(
    catalog: RetrievalRepository, identity: Identity, names: list[str]
) -> tuple[CatalogSource, ...]:
    """The workspace's sources with searchable documents, only the named ones if any.

    Raises:
        InvalidSetup: The catalog cannot be read, a named source does not exist, or no
            chosen source has a searchable document.
    """
    try:
        available = catalog.eligible(identity, None)
    except (ContextMeshError, SQLAlchemyError) as error:
        raise InvalidSetup(
            f"cannot read the sources of workspace {identity.workspace_id} "
            f"({failure_code(error)}); is the database up, and has the API started once?"
        ) from None
    by_name = {source.name.casefold(): source for source in available}
    unknown = [name for name in names if name.casefold() not in by_name]
    if unknown:
        raise InvalidSetup(f"unknown source: {', '.join(unknown)}")
    chosen = [by_name[name.casefold()] for name in names] if names else available
    searchable = tuple(source for source in chosen if source.searchable_count > 0)
    if not searchable:
        raise InvalidSetup("no searchable source: upload evals/corpus and wait for indexing")
    return searchable


# Report


def written(report: Report, output: Path) -> Path:
    """Write `report.json` and `report.md` into `output`, creating it, and return it."""
    output.mkdir(parents=True, exist_ok=True)
    document = json.dumps(report_document(report), indent=2, ensure_ascii=False)
    (output / "report.json").write_text(document + "\n", encoding="utf-8")
    (output / "report.md").write_text(markdown(report), encoding="utf-8")
    return output


def report_document(report: Report) -> dict[str, object]:
    """The JSON report: the run's settings, the summary, the thresholds, and every run."""
    summary = report.summary
    return {
        "dataset": str(report.dataset),
        "started_at": report.started_at.isoformat(),
        "models": dict(report.models),
        "sources": [source.name for source in report.plan.catalog],
        "repeats": report.plan.repeats,
        "summary": {
            "runs": summary.runs,
            "agent_errors": summary.agent_errors,
            "judge_errors": summary.judge_errors,
            "judge_tokens": summary.judge_usage.total,
            "overall": dict(summary.overall),
            "by_tag": {tag: dict(means) for tag, means in summary.by_tag.items()},
        },
        "thresholds": dict(report.thresholds),
        "failed_thresholds": dict(report.failed),
        "runs": [run_record(result) for result in report.results],
    }


def run_record(result: CaseResult) -> dict[str, object]:
    """One run as JSON: its case, scores, and shortfalls, then what the agent answered."""
    record: dict[str, object] = {
        "case_id": result.case.id,
        "repeat": result.repeat,
        "tags": list(result.case.tags),
        "metrics": dict(result.metrics),
        "agent_error": result.agent_error,
        "judge_errors": dict(result.judge_errors),
        "judge_tokens": result.judge_usage.total,
        "shortfalls": list(result.shortfalls),
    }
    if result.outcome is not None:
        record.update(answered(result.outcome))
    return record


def answered(outcome: AgentOutcome) -> dict[str, object]:
    """What the agent answered, the documents it cited, and why, from its trace."""
    answer = outcome.answer
    return {
        "status": answer.status,
        "answer": answer.text,
        "cited_documents": sorted({citation.title for citation in answer.citations}),
        "gaps": list(answer.gaps),
        "trace": [f"{stage.stage}: {stage.summary}" for stage in outcome.trace],
    }


def markdown(report: Report) -> str:
    """The human report: settings, metric means, means per tag, thresholds, failing runs."""
    summary, plan = report.summary, report.plan
    by_tag = [
        [tag, *(formatted(means.get(name)) for name in QUALITY_METRICS)]
        for tag, means in summary.by_tag.items()
    ]
    lines = [
        f"# Agent evaluation: {report.dataset.stem}",
        "",
        f"- Started: {report.started_at:%Y-%m-%d %H:%M:%S} UTC",
        f"- Runs: {summary.runs} ({len(plan.cases)} cases × {plan.repeats})",
        f"- Agent model: `{report.models['agent']}`; judge model: `{report.models['judge']}`",
        f"- Sources: {', '.join(source.name for source in plan.catalog)}",
        f"- Agent errors: {summary.agent_errors}; judge errors: {summary.judge_errors}; "
        f"judge tokens: {summary.judge_usage.total}",
        "",
        "## Metrics",
        "",
        *table(
            ["Metric", "Mean"], [[name, formatted(mean)] for name, mean in summary.overall.items()]
        ),
        "",
        "## By tag",
        "",
        *(table(["Tag", *QUALITY_METRICS], by_tag) if by_tag else ["No case has a tag."]),
        "",
        "## Thresholds",
        "",
        *threshold_lines(report),
        "",
        "## Failing runs",
        "",
        *failing_lines(report.results),
    ]
    return "\n".join(lines) + "\n"


def threshold_lines(report: Report) -> list[str]:
    """One line per `--fail-under` threshold, saying whether its mean reached it."""
    if not report.thresholds:
        return ["No threshold was set (`--fail-under METRIC=VALUE`)."]
    return [
        f"- {'FAILED' if name in report.failed else 'ok'}: `{name}` mean "
        f"{formatted(report.summary.overall.get(name))}, minimum {minimum:.2f}"
        for name, minimum in report.thresholds.items()
    ]


def failing_lines(results: tuple[CaseResult, ...]) -> list[str]:
    """One line per run with a shortfall, naming the case, the run, and the reasons."""
    lines = [
        f"- `{result.case.id}` run {result.repeat}: {'; '.join(result.shortfalls)}"
        for result in results
        if result.shortfalls
    ]
    return lines or ["Every run scored 1 on every quality metric that applied."]


def table(header: list[str], rows: list[list[str]]) -> list[str]:
    """A Markdown table."""
    return [
        f"| {' | '.join(header)} |",
        f"|{' --- |' * len(header)}",
        *(f"| {' | '.join(row)} |" for row in rows),
    ]


def formatted(value: float | None) -> str:
    """A mean with two decimals, or `n/a` when the metric never applied."""
    return "n/a" if value is None else f"{value:.2f}"


# LangSmith


def published(experiments: LangSmithExperiments, report: Report) -> str:
    """Sync the dataset to LangSmith and log the run as an experiment; returns its name."""
    dataset = report.dataset.stem
    examples = experiments.synced(dataset, {case.id: example(case) for case in report.plan.cases})
    runs: dict[str, list[dict[str, object]]] = {}
    for result in report.results:
        runs.setdefault(result.case.id, []).append(run_record(result))
    metadata = {"dataset_file": str(report.dataset), **report.models}
    experiment = Experiment(dataset, examples, runs, report.plan.repeats, metadata)
    name = experiments.logged(experiment)
    flow_event(FLOW, "experiment_logged", examples=len(examples), runs=len(report.results))
    return name


def example(case: EvalCase) -> dict[str, object]:
    """The case as a LangSmith example: the agent's inputs, the expectations, and its tags."""
    return {
        "inputs": {
            "case_id": case.id,
            "question": case.question,
            "history": [asdict(turn) for turn in case.history],
        },
        "outputs": {
            "expected_route": case.expected_route,
            "answerable": case.answerable,
            "expected_documents": list(case.expected_documents),
            "expected_quotes": list(case.expected_quotes),
            "reference_answer": case.reference_answer,
        },
        "metadata": {"tags": list(case.tags)},
    }


if __name__ == "__main__":
    sys.exit(main())
