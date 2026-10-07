"""Deterministic test doubles: hashed embeddings and a scripted structured model."""

import hashlib
import json
import math
import re
from collections.abc import Callable
from uuid import uuid4

from app.bootstrap.services import ExternalServices, collection_name
from app.domain.answers import Answer, TraceStage
from app.domain.models import AgentOutcome, Usage
from app.search.qdrant import QdrantVectorIndex
from app.services.ports.models import StructuredReply, StructuredTask
from qdrant_client import QdrantClient

ROOT = "/api/v1/assistant"
WORD = re.compile(r"[a-z0-9]+")
DIMENSIONS = 64


class HashEmbeddings:
    """Bag-of-words hashing: shared words give similar vectors; not semantic quality."""

    identity = "test:hashed-words"

    def __init__(self):
        self.calls: list[tuple[str, ...]] = []
        self.failure: Exception | None = None

    def embed(self, texts):
        self.calls.append(tuple(texts))
        if self.failure is not None:
            raise self.failure
        return tuple(_vector(text) for text in texts)


def _vector(text: str) -> tuple[float, ...]:
    values = [0.0] * DIMENSIONS
    for word in WORD.findall(text.lower()):
        values[int(hashlib.sha256(word.encode()).hexdigest(), 16) % DIMENSIONS] += 1.0
    return _normalized(values)


def _normalized(values: list[float]) -> tuple[float, ...]:
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return tuple(value / norm for value in values)


def plan_all_sources(content: dict) -> dict:
    return {
        "standalone_question": content["question"],
        "source_ids": [source["id"] for source in content["sources"]],
        "queries": [content["question"]],
    }


def sufficient(content: dict) -> dict:
    return {"sufficient": True, "gaps": [], "queries": [], "expand_source_ids": []}


def cite_first_passage(content: dict) -> dict:
    passage = content["passages"][0]
    sentence = passage["text"].split("\n")[0][:200]
    return {
        "status": "answered",
        "claims": [{"text": f"The source states: {sentence}", "evidence_ids": [passage["id"]]}],
        "gaps": [],
    }


def all_supported(content: dict) -> dict:
    return {
        "verdicts": [{"index": claim["index"], "supported": True} for claim in content["claims"]]
    }


DEFAULT_SCRIPT: dict[str, Callable[[dict], dict]] = {
    "search_plan": plan_all_sources,
    "evidence_assessment": sufficient,
    "grounded_answer": cite_first_passage,
    "claim_support": all_supported,
}


class ScriptedReasoning:
    """Answers each structured task by name; records every task for assertions."""

    def __init__(self):
        self.configured = True
        self.tasks: list[StructuredTask] = []
        self.script = dict(DEFAULT_SCRIPT)
        self.failure: Exception | None = None

    def complete(self, task: StructuredTask) -> StructuredReply:
        self.tasks.append(task)
        if self.failure is not None:
            raise self.failure
        content = json.loads(task.content)
        return StructuredReply(self.script[task.name](content), Usage(10, 5))

    def names(self) -> list[str]:
        return [task.name for task in self.tasks]

    def contents(self, name: str) -> list[dict]:
        return [json.loads(task.content) for task in self.tasks if task.name == name]


def memory_services() -> tuple[ExternalServices, ScriptedReasoning, HashEmbeddings]:
    reasoning = ScriptedReasoning()
    embeddings = HashEmbeddings()
    vectors = QdrantVectorIndex(
        QdrantClient(location=":memory:"), collection_name(embeddings.identity)
    )
    return ExternalServices(reasoning, embeddings, vectors), reasoning, embeddings


def drain(worker, limit=20):
    for _ in range(limit):
        if not worker.run_once():
            return


def create_source(client, name="Architecture", description="Decision records"):
    response = client.post("/api/v1/sources", json={"name": name, "description": description})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def upload(client, source_id, filename, text):
    files = {"file": (filename, text.encode(), "text/markdown")}
    return client.post(f"/api/v1/sources/{source_id}/documents", files=files)


def documents(client, source_id):
    return client.get(f"/api/v1/sources/{source_id}/documents").json()["items"]


def ask(client, conversation_id, message, key=None, source_ids=None):
    body = {"message": message}
    if source_ids is not None:
        body["source_ids"] = source_ids
    return client.post(
        f"{ROOT}/conversations/{conversation_id}/messages",
        json=body,
        headers={"Idempotency-Key": key or str(uuid4())},
    )


def fixture_outcome(text="Fixture grounded reply"):
    answer = Answer("insufficient_evidence", text, (), (), ("Fixture gap",))
    return AgentOutcome(answer, (TraceStage("release", "Fixture release."),), Usage(10, 4))


class FixtureWorkflow:
    """Stands in for the graph when a test targets turn persistence, not agent behavior."""

    def __init__(self, outcome=None):
        self.outcome = outcome or fixture_outcome()
        self.calls = []

    def run(self, identity, question, history, catalog):
        self.calls.append((question, history, catalog))
        return self.outcome
