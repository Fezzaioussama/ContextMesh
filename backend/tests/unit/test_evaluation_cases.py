"""Golden dataset loading and the deterministic metrics of one evaluated run."""

import json

import pytest
from app.services.evaluation.cases import (
    BUDGET_METRICS,
    DETERMINISTIC_QUALITY,
    EvalCase,
    InvalidDataset,
    abstention_correct,
    document_precision,
    document_recall,
    load_cases,
    quote_recall,
    route_correct,
    scored,
)
from support import cited_outcome, evidence

PLANS = evidence("tidepool-plans.md", "Harbor costs $14 per user\nper month billed annually.")
SUPPORT = evidence("tidepool-support.md", "Lighthouse P1 target: 30 minutes.")


def case(**fields):
    values = {"id": "c1", "question": "How much is Harbor?", "expected_route": "retrieve"}
    return EvalCase(**{**values, "answerable": True, **fields})


def write_lines(tmp_path, *lines):
    path = tmp_path / "golden.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


VALID = json.dumps(
    {
        "id": "follow-up",
        "question": " And on Lighthouse? ",
        "expected_route": "retrieve",
        "answerable": True,
        "history": [{"role": "user", "content": "How much is Harbor?"}],
        "expected_documents": ["tidepool-plans.md"],
        "tags": ["pricing"],
    }
)


def test_loader_reads_valid_cases_and_skips_blank_lines(tmp_path):
    (loaded,) = load_cases(write_lines(tmp_path, "", VALID, "   "))
    messages = loaded.messages()
    assert (loaded.id, loaded.question, loaded.tags, loaded.reference_answer) == (
        "follow-up",
        "And on Lighthouse?",
        ("pricing",),
        "",
    )
    assert [(message.role, message.content) for message in messages] == [
        ("user", "How much is Harbor?")
    ]


@pytest.mark.parametrize(
    ("line", "problem"),
    [
        ("{not json", "line: Invalid JSON"),
        (
            '{"id": "x", "question": "q", "expected_route": "web", "answerable": true}',
            "expected_route",
        ),
        (
            '{"id": "x", "question": "q", "expected_route": "direct", "answerable": "yes"}',
            "answerable",
        ),
        ('{"id": "", "question": "q", "expected_route": "direct", "answerable": true}', "id"),
        ('{"id": "x", "question": "q", "expected_route": "direct"}', "answerable"),
        (
            '{"id": "x", "question": "q", "expected_route": "direct", "answerable": true, "x": 1}',
            "x: Unexpected",
        ),
        (
            '{"id": "x", "question": "q", "expected_route": "direct", "answerable": true, '
            '"history": [{"role": "bot", "content": "hi"}]}',
            "history.0.role",
        ),
    ],
)
def test_a_bad_line_names_its_line_number_and_field(tmp_path, line, problem):
    path = write_lines(tmp_path, VALID, line)
    with pytest.raises(InvalidDataset) as failure:
        load_cases(path)
    assert str(failure.value).startswith(f"{path}:2: ")
    assert problem in str(failure.value)


def test_a_repeated_case_id_is_rejected_on_its_line(tmp_path):
    with pytest.raises(InvalidDataset, match=r"golden\.jsonl:3: case id 'follow-up'"):
        load_cases(write_lines(tmp_path, VALID, "", VALID))


def test_an_empty_dataset_is_rejected(tmp_path):
    with pytest.raises(InvalidDataset, match="holds no case"):
        load_cases(write_lines(tmp_path, "", " "))


def test_route_is_direct_only_for_a_direct_response():
    direct, grounded = cited_outcome(status="direct").answer, cited_outcome(status="partial").answer
    assert [
        route_correct(case(expected_route="direct"), direct),
        route_correct(case(expected_route="direct"), grounded),
        route_correct(case(), grounded),
    ] == [True, False, True]


@pytest.mark.parametrize(
    ("answerable", "route", "status", "expected"),
    [
        (False, "retrieve", "insufficient_evidence", True),
        (False, "retrieve", "partial", True),
        (False, "retrieve", "answered", False),
        (True, "retrieve", "answered", True),
        (True, "retrieve", "partial", True),
        (True, "retrieve", "insufficient_evidence", False),
        (True, "direct", "direct", None),
    ],
)
def test_abstention_accepts_partial_answers_both_ways(answerable, route, status, expected):
    answer = cited_outcome(status=status).answer
    assert abstention_correct(case(answerable=answerable, expected_route=route), answer) is expected


def test_document_recall_and_precision_compare_cited_titles_ignoring_case():
    answer = cited_outcome((PLANS, SUPPORT, PLANS)).answer
    expected = case(expected_documents=("TIDEPOOL-plans.md", "tidepool-release-notes.md"))
    assert (document_recall(expected, answer), document_precision(expected, answer)) == (0.5, 0.5)


def test_document_metrics_do_not_apply_without_expected_or_cited_documents():
    cited, uncited = cited_outcome((PLANS,)).answer, cited_outcome().answer
    expected = case(expected_documents=("tidepool-plans.md",))
    assert [
        document_recall(case(), cited),
        document_precision(case(), cited),
        document_precision(expected, uncited),
        document_recall(expected, uncited),
    ] == [None, None, None, 0.0]


def test_quote_recall_searches_the_drafted_context_ignoring_case_and_spacing():
    quotes = case(expected_quotes=("$14 PER USER   per month", "Lighthouse P1", "Code Amber"))
    assert quote_recall(quotes, (SUPPORT, PLANS)) == pytest.approx(2 / 3)
    assert (quote_recall(quotes, ()), quote_recall(case(), (PLANS,))) == (0.0, None)


def test_scored_reports_every_deterministic_metric_with_the_budget():
    stages = ("route", "plan", "retrieve", "assess", "expand", "retrieve", "answer", "repair")
    outcome = cited_outcome((PLANS,), (PLANS,), stages=stages)
    metrics = scored(case(expected_documents=("tidepool-plans.md",)), outcome, 1.23456)
    assert tuple(metrics) == (*DETERMINISTIC_QUALITY, *BUDGET_METRICS)
    assert {name: metrics[name] for name in BUDGET_METRICS} == {
        "total_tokens": 120,
        "latency_seconds": 1.235,
        "search_rounds": 2,
        "repairs": 1,
    }
    assert (metrics["document_recall"], metrics["quote_recall"]) == (1.0, None)


def test_a_file_that_is_not_utf8_is_an_invalid_dataset(tmp_path):
    path = tmp_path / "latin1.jsonl"
    path.write_bytes(VALID.replace("Lighthouse", "Lighth\u00f4use").encode("latin-1"))
    with pytest.raises(InvalidDataset, match=r"latin1\.jsonl: the file is not UTF-8 text\."):
        load_cases(path)


def test_a_blank_reference_answer_is_stripped_to_empty(tmp_path):
    line = json.dumps({**json.loads(VALID), "reference_answer": "  \n "})
    (loaded,) = load_cases(write_lines(tmp_path, line))
    assert loaded.reference_answer == ""
