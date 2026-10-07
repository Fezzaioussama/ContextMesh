"""Release-time and history visibility: evidence removed mid-turn or after saving."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from support import all_supported, ask, create_source, documents, drain, upload

pytestmark = pytest.mark.integration
ROOT = "/api/v1/assistant"
GUIDE = "# Guide\n\nKey rotation happens weekly."


@pytest.fixture
def indexed(client, worker):
    source_id = create_source(client)
    upload(client, source_id, "guide.md", GUIDE)
    drain(worker)
    return source_id, documents(client, source_id)[0]["id"]


def history(client, conversation_id):
    return client.get(f"{ROOT}/conversations/{conversation_id}/messages").json()["items"]


def removal_path(target, source_id, document_id):
    if target == "source":
        return f"/api/v1/sources/{source_id}"
    return f"/api/v1/documents/{document_id}"


def paused_verification(reasoning):
    """Hold the support check so the test can remove evidence before release."""
    entered, release = Event(), Event()

    def verify(content):
        entered.set()
        release.wait(timeout=10)
        return all_supported(content)

    reasoning.script["claim_support"] = verify
    return entered, release


@pytest.mark.parametrize("target", ["document", "source"])
def test_evidence_removed_during_the_turn_is_never_released(
    client, conversation_id, reasoning, indexed, target
):
    entered, release = paused_verification(reasoning)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(ask, client, conversation_id, "key rotation", "turn-key")
        entered.wait(timeout=10)
        removed = client.delete(removal_path(target, *indexed)).status_code
        release.set()
        response = pending.result(timeout=10)
    saved = history(client, conversation_id)
    assert (removed, response.status_code, response.json()["error"]["code"]) == (
        202,
        409,
        "evidence_changed",
    )
    assert [item["role"] for item in saved] == ["user"]


def test_retry_after_an_evidence_change_runs_the_agent_again(
    client, conversation_id, reasoning, indexed
):
    entered, release = paused_verification(reasoning)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(ask, client, conversation_id, "key rotation", "turn-key")
        entered.wait(timeout=10)
        client.delete(removal_path("document", *indexed))
        release.set()
        pending.result(timeout=10)
    retry = ask(client, conversation_id, "key rotation", "turn-key").json()
    answer = retry["assistant_message"]["answer"]
    assert (answer["status"], len(history(client, conversation_id))) == (
        "insufficient_evidence",
        2,
    )


def test_completed_scoped_turn_replays_after_its_source_is_deleted(
    client, conversation_id, indexed
):
    source_id, _document_id = indexed
    first = ask(client, conversation_id, "key rotation", "turn-key", [source_id])
    client.delete(f"/api/v1/sources/{source_id}")
    replay = ask(client, conversation_id, "key rotation", "turn-key", [source_id])
    status = replay.json()["assistant_message"]["answer"]["status"]
    assert (first.status_code, replay.status_code, status) == (200, 200, "withheld")


def test_uncited_answers_built_from_deleted_documents_are_withheld(
    client, conversation_id, reasoning, indexed
):
    reasoning.script["grounded_answer"] = lambda content: {
        "status": "insufficient_evidence",
        "claims": [],
        "gaps": ["Paraphrase of what the passage says about rotation."],
    }
    ask(client, conversation_id, "key rotation", "turn-key")
    before = history(client, conversation_id)[-1]["answer"]["status"]
    client.delete(removal_path("document", *indexed))
    after = history(client, conversation_id)[-1]
    assert (before, after["answer"]["status"], after["answer"]["gaps"]) == (
        "insufficient_evidence",
        "withheld",
        [],
    )
