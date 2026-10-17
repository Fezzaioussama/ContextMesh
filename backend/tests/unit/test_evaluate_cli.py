"""The evaluation command: exit codes, reports, and selection, without network or database."""

import json
from uuid import uuid4

import pytest
from app import evaluate
from app.services.agent.state import AgentPolicy
from app.services.models import CatalogSource
from app.utils.config import Settings
from app.utils.errors import ContextMeshError
from app.wiring import Assistant, Evaluation
from sqlalchemy.exc import OperationalError
from support import FixtureWorkflow, ScriptedReasoning, cited_outcome, evidence

PLANS = evidence("tidepool-plans.md", "Harbor costs $14 per user per month.")
TIDEPOOL = CatalogSource(uuid4(), "Tidepool", "Plans", 3)
EMPTY = CatalogSource(uuid4(), "Drafts", "Nothing indexed", 0)
CASES = [
    {
        "id": "harbor-price",
        "question": "How much is Harbor?",
        "expected_route": "retrieve",
        "answerable": True,
        "expected_documents": ["tidepool-plans.md"],
        "expected_quotes": ["$14 per user"],
        "reference_answer": "$14 per user per month.",
        "tags": ["pricing"],
    },
    {
        "id": "ceo",
        "question": "Who is the CEO?",
        "expected_route": "retrieve",
        "answerable": False,
        "expected_quotes": ["CEO"],
        "tags": ["unanswerable"],
    },
]


class Catalog:
    def __init__(self, *sources, failure=None):
        self.sources = sources
        self.failure = failure

    def eligible(self, identity, source_ids):
        if self.failure is not None:
            raise self.failure
        return self.sources


def judge_model():
    model = ScriptedReasoning()
    model.script["judge_faithfulness"] = lambda content: {
        "verdicts": [{"claim_id": claim["id"], "supported": True} for claim in content["claims"]]
    }
    model.script["judge_correctness"] = lambda content: {"grade": 3}
    return model


@pytest.fixture(autouse=True)
def quiet_flows(monkeypatch):
    """main() installs a stderr flow handler for good; tests read flow lines from `flow_lines`."""
    monkeypatch.setattr(evaluate, "show_flows", lambda: None)


@pytest.fixture
def workflow():
    return FixtureWorkflow(cited_outcome((PLANS,), (PLANS,)))


@pytest.fixture
def catalog(monkeypatch, workflow):
    """Fakes the composition; returns the catalog so a test can change its sources."""
    fake = Catalog(TIDEPOOL, EMPTY)
    assistant = Assistant(workflow, fake, ScriptedReasoning(), AgentPolicy())
    composed = Evaluation(assistant, judge_model())
    monkeypatch.setattr(evaluate, "build_evaluation", lambda config, resources: composed)
    return fake


@pytest.fixture
def dataset(tmp_path):
    path = tmp_path / "golden.jsonl"
    path.write_text("\n".join(json.dumps(case) for case in CASES) + "\n", encoding="utf-8")
    return path


def settings(**fields):
    values = {
        "openrouter_api_key": "router-key",
        "openrouter_model": "agent/model",
        "openrouter_judge_model": "judge/model",
        "langsmith_tracing": False,
        "langsmith_api_key": "",
    }
    return Settings(_env_file=None, **{**values, **fields})


def run(dataset, tmp_path, *options, config=None):
    arguments = ["--dataset", str(dataset), "--output", str(tmp_path / "report"), *options]
    return evaluate.main(arguments, config or settings())


def test_a_failed_threshold_exits_1_after_writing_both_reports(dataset, tmp_path, catalog):
    code = run(dataset, tmp_path, "--fail-under", "quote_recall=1", "--fail-under", "completed=1")
    report = json.loads((tmp_path / "report" / "report.json").read_text())
    summary = (tmp_path / "report" / "report.md").read_text()
    assert (code, report["failed_thresholds"], report["sources"]) == (
        1,
        {"quote_recall": 0.5},
        ["Tidepool"],
    )
    assert [run["case_id"] for run in report["runs"]] == ["harbor-price", "ceo"]
    assert report["runs"][0]["metrics"]["correctness"] == 1.0
    assert report["runs"][0]["cited_documents"] == ["tidepool-plans.md"]
    assert "- FAILED: `quote_recall` mean 0.50, minimum 1.00" in summary
    assert "- ok: `completed` mean 1.00, minimum 1.00" in summary
    assert "- `ceo` run 1: abstention_correct=0.00; quote_recall=0.00" in summary
    assert "| pricing | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |" in summary


def test_holding_thresholds_exit_0_and_a_same_model_judge_is_warned_about(
    dataset, tmp_path, catalog, capsys
):
    code = run(
        dataset,
        tmp_path,
        "--fail-under",
        "faithfulness=1",
        config=settings(openrouter_judge_model=""),
    )
    assert code == 0
    assert "a model grading its own answers is biased" in capsys.readouterr().err


def test_tags_and_sources_select_what_runs(dataset, tmp_path, catalog, workflow):
    catalog.sources = (TIDEPOOL, CatalogSource(uuid4(), "Other", "More", 1))
    assert run(dataset, tmp_path, "--tag", "pricing", "--source", "tidepool", "--repeats", "2") == 0
    assert [(question, sources) for question, _, sources in workflow.calls] == [
        ("How much is Harbor?", (TIDEPOOL,)),
        ("How much is Harbor?", (TIDEPOOL,)),
    ]


def test_an_invalid_dataset_line_exits_2_naming_the_line(dataset, tmp_path, catalog, capsys):
    dataset.write_text(json.dumps(CASES[0]) + '\n{"id": "x"}\n', encoding="utf-8")
    assert run(dataset, tmp_path) == 2
    assert f"error: {dataset}:2: " in capsys.readouterr().err


@pytest.mark.parametrize(
    ("options", "config", "message"),
    [
        ((), settings(openrouter_api_key=""), "set OPENROUTER_API_KEY"),
        (("--langsmith",), settings(), "--langsmith needs LANGSMITH_API_KEY"),
        (("--tag", "nothing-has-this"), settings(), "no case in"),
    ],
    ids=["no-openrouter-key", "no-langsmith-key", "no-matching-tag"],
)
def test_missing_setup_exits_2_before_any_run(dataset, tmp_path, options, config, message, capsys):
    assert run(dataset, tmp_path, *options, config=config) == 2
    assert message in capsys.readouterr().err


@pytest.mark.parametrize(
    ("sources", "failure", "options", "message"),
    [
        ((EMPTY,), None, (), "no searchable source"),
        ((TIDEPOOL,), None, ("--source", "Missing"), "unknown source: Missing"),
        ((), ContextMeshError("not_found"), (), "cannot read the sources"),
        ((), OperationalError("x", None, None), (), "(OperationalError); is the database up"),
    ],
    ids=["nothing-indexed", "unknown-source", "unknown-workspace", "database-down"],
)
def test_an_unusable_catalog_exits_2(
    dataset, tmp_path, catalog, workflow, sources, failure, options, message, capsys
):
    catalog.sources, catalog.failure = sources, failure
    assert run(dataset, tmp_path, *options) == 2
    assert (message in capsys.readouterr().err, workflow.calls) == (True, [])


def test_invalid_settings_exit_2_without_showing_any_value(
    dataset, tmp_path, catalog, monkeypatch, capsys
):
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-secret-value")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "")
    arguments = ["--dataset", str(dataset), "--output", str(tmp_path / "report")]
    assert evaluate.main(arguments) == 2
    error = capsys.readouterr().err
    assert "invalid settings: settings: Value error, LANGSMITH_TRACING=true requires" in error
    assert "router-secret-value" not in error


def test_a_dataset_that_is_not_utf8_exits_2(dataset, tmp_path, catalog, capsys):
    dataset.write_bytes(json.dumps(CASES[0], ensure_ascii=False).encode("utf-16"))
    assert run(dataset, tmp_path) == 2
    assert "the file is not UTF-8 text" in capsys.readouterr().err


def test_a_missing_dataset_file_exits_2(tmp_path, capsys):
    assert run(tmp_path / "absent.jsonl", tmp_path) == 2
    assert "cannot read" in capsys.readouterr().err


@pytest.mark.parametrize(
    "option",
    [
        ("--fail-under", "latency_seconds=10"),
        ("--fail-under", "faithfulness=high"),
        ("--fail-under", "faithfulness=1.5"),
        ("--repeats", "0"),
    ],
)
def test_invalid_options_exit_2(dataset, tmp_path, option):
    with pytest.raises(SystemExit) as stopped:
        run(dataset, tmp_path, *option)
    assert stopped.value.code == 2


class RecordingExperiments:
    def __init__(self):
        self.synced_with = None
        self.experiment = None

    def synced(self, dataset, examples):
        self.synced_with = (dataset, examples)
        return ("example-a", "example-b")

    def logged(self, experiment):
        self.experiment = experiment
        return "golden-1a2b"


def test_langsmith_receives_the_examples_and_every_recorded_run(
    dataset, tmp_path, catalog, monkeypatch, capsys
):
    experiments = RecordingExperiments()
    monkeypatch.setattr(evaluate, "create_experiments", lambda config, resources: experiments)
    config = settings(langsmith_api_key="ls-key")
    assert run(dataset, tmp_path, "--langsmith", "--repeats", "2", config=config) == 0
    name, examples = experiments.synced_with
    harbor = examples["harbor-price"]
    experiment = experiments.experiment
    assert (name, list(examples), harbor["inputs"]["case_id"]) == (
        "golden",
        ["harbor-price", "ceo"],
        "harbor-price",
    )
    assert (harbor["outputs"]["expected_quotes"], harbor["metadata"]) == (
        ["$14 per user"],
        {"tags": ["pricing"]},
    )
    assert (experiment.name, experiment.repeats, experiment.examples) == (
        "golden",
        2,
        ("example-a", "example-b"),
    )
    assert [record["repeat"] for record in experiment.runs["ceo"]] == [1, 2]
    assert experiment.runs["ceo"][0]["metrics"]["abstention_correct"] is False
    assert experiment.metadata == {
        "dataset_file": str(dataset),
        "agent": "agent/model",
        "judge": "judge/model",
    }
    assert "LangSmith experiment: golden-1a2b" in capsys.readouterr().out


@pytest.mark.parametrize(("options", "traced"), [((), False), (("--langsmith",), True)])
def test_turn_tracing_reaches_langsmith_only_with_the_flag(
    dataset, tmp_path, catalog, monkeypatch, options, traced
):
    composed, configs = evaluate.build_evaluation(None, None), []

    def build(config, resources):
        configs.append(config)
        return composed

    monkeypatch.setattr(evaluate, "build_evaluation", build)
    monkeypatch.setattr(
        evaluate, "create_experiments", lambda config, resources: RecordingExperiments()
    )
    config = settings(langsmith_tracing=True, langsmith_api_key="ls-key")
    assert run(dataset, tmp_path, *options, config=config) == 0
    assert [item.langsmith_tracing for item in configs] == [traced]


def test_a_failure_after_the_report_exits_3_naming_only_its_code(
    dataset, tmp_path, catalog, monkeypatch, capsys, flow_lines
):
    experiments = RecordingExperiments()

    def unreachable(dataset, examples):
        raise ConnectionError("token ls-key rejected")

    experiments.synced = unreachable
    monkeypatch.setattr(evaluate, "create_experiments", lambda config, resources: experiments)
    config = settings(langsmith_api_key="ls-key")
    assert (
        run(dataset, tmp_path, "--langsmith", "--fail-under", "quote_recall=1", config=config) == 3
    )
    output = capsys.readouterr()
    assert (tmp_path / "report" / "report.json").is_file()
    assert "error: the evaluation failed (ConnectionError)" in output.err
    assert "ls-key" not in output.err + output.out
    assert flow_lines[-1] == "flow=evaluate_agent step=failed code=ConnectionError"
