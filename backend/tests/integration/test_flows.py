"""Each user story leaves `flow=… step=…` lines that follow it end to end, without content."""

import logging

import pytest
from app.utils.logging import flow_logger
from support import ask, create_source, drain, upload

pytestmark = pytest.mark.integration
QUESTION = "What does the handbook say about secrets?"


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


def steps(lines, flow):
    prefix = f"flow={flow} step="
    return [line[len(prefix) :].split()[0] for line in lines if line.startswith(prefix)]


def test_upload_and_question_flows_can_be_followed_in_the_logs(
    client, worker, conversation_id, flows
):
    source_id = create_source(client)
    upload(client, source_id, "handbook.md", "# Handbook\n\nSecrets rotate every month.")
    drain(worker)
    ask(client, conversation_id, QUESTION)
    assert steps(flows.lines, "upload_document") == ["accepted", "job_started", "job_finished"]
    assert steps(flows.lines, "ask_question")[0] == "turn_started"
    assert steps(flows.lines, "ask_question")[-1] == "answered"
    assert "agent_plan" in steps(flows.lines, "ask_question")
    assert not any("Secrets rotate" in line or QUESTION in line for line in flows.lines)
