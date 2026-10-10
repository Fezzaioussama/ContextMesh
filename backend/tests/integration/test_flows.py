"""Each user story leaves `flow=… step=…` lines that follow it end to end, without content."""

import logging
import re
from uuid import uuid4

import pytest
from app.utils.logging import flow_logger
from support import ask, create_source, drain, upload

pytestmark = pytest.mark.integration
QUESTION = "What does the handbook say about secrets?"
FACT = "Secrets rotate every month."


class Collector(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def flows():
    collector = Collector()
    flow_logger.addHandler(collector)
    flow_logger.setLevel(logging.INFO)
    yield collector
    flow_logger.removeHandler(collector)


@pytest.fixture
def answered(client, worker, conversation_id, flows):
    source_id = create_source(client)
    upload(client, source_id, "handbook.md", f"# Handbook\n\n{FACT}")
    drain(worker)
    ask(client, conversation_id, QUESTION)
    return flows.lines


def lines_of(lines, flow):
    return [line for line in lines if line.startswith(f"flow={flow} ")]


def steps(lines, flow):
    return [re.search(r"step=(\S+)", line).group(1) for line in lines_of(lines, flow)]


def test_an_upload_is_followed_from_acceptance_to_the_end_of_its_job(answered):
    assert steps(answered, "upload_document") == ["accepted", "job_started", "job_ended"]


def test_every_line_of_a_question_carries_its_turn(answered):
    asked = lines_of(answered, "ask_question")
    turns = {re.search(r"turn_id=(\S+)", line).group(1) for line in asked}
    expected = (["turn_started", "agent_plan"], "answered", 1)
    assert (steps(asked, "ask_question")[:2], steps(asked, "ask_question")[-1], len(turns)) == (
        expected
    )


def test_flow_lines_never_contain_questions_or_document_text(answered):
    assert [line for line in answered if QUESTION in line or FACT in line] == []


def test_questions_refused_before_a_turn_starts_are_logged(client, conversation_id, flows):
    ask(client, conversation_id, QUESTION, source_ids=[str(uuid4())])
    assert steps(flows.lines, "ask_question") == ["rejected"]
