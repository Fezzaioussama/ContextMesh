"""HTTP persistence behavior for the pre-retrieval decision route."""

from dataclasses import replace

from app.services.ports.decisions import ChoiceReply
from app.services.rules.models import Usage
from app.setup.api import create_app
from fastapi.testclient import TestClient
from support import ask


class DirectDecision:
    configured = True

    def __init__(self):
        self.tasks = []

    def choose(self, task):
        self.tasks.append(task)
        return ChoiceReply("direct", {"direct": 0.95, "retrieve": 0.05}, 0.95, Usage(3, 1))


def test_direct_question_skips_retrieval_and_persists_without_citations(
    settings, identity, services, reasoning
):
    decisions = DirectDecision()
    reasoning.script["direct_answer"] = lambda content: {"answer": "Hello! How can I help?"}
    routed = replace(services, decisions=decisions)

    with TestClient(create_app(settings, routed, identity)) as client:
        conversation = client.post("/api/v1/assistant/conversations", json={}).json()["id"]
        response = ask(client, conversation, "Hello")
        saved = client.get(f"/api/v1/assistant/conversations/{conversation}/messages")

    _assert_direct_response(response)
    _assert_route_execution(response, reasoning, decisions)
    assert saved.json()["items"][-1]["answer"]["status"] == "direct"


def _assert_direct_response(response):
    assert response.status_code == 200
    answer = response.json()["assistant_message"]["answer"]
    assert (answer["status"], answer["citations"], answer["claims"]) == ("direct", [], [])
    assert response.json()["assistant_message"]["content"] == "Hello! How can I help?"


def _assert_route_execution(response, reasoning, decisions):
    assert [item["stage"] for item in response.json()["trace"]] == ["route", "direct", "release"]
    assert reasoning.names() == ["direct_answer"]
    assert len(decisions.tasks) == 1
