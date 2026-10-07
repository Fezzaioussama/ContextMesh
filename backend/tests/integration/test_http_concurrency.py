"""The HTTP transport stays responsive while an agent execution holds its claim."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from support import ask, create_source, drain, plan_all_sources, upload

pytestmark = pytest.mark.integration
ROOT = "/api/v1/assistant"


def test_blocked_agent_allows_health_checks_and_rejects_concurrent_turn(client, worker, reasoning):
    entered, release = Event(), Event()

    def waiting_plan(content):
        entered.set()
        release.wait(timeout=5)
        return plan_all_sources(content)

    reasoning.script["search_plan"] = waiting_plan
    upload(client, create_source(client), "guide.md", "# Guide\n\nHello world.")
    drain(worker)
    conversation_id = client.post(f"{ROOT}/conversations", json={}).json()["id"]
    with ThreadPoolExecutor(max_workers=1) as pool:
        original = pool.submit(ask, client, conversation_id, "Hello world", "first")
        entered.wait(timeout=5)
        live = client.get("/health/live")
        conflict = ask(client, conversation_id, "Hello world", "second")
        history = client.get(f"{ROOT}/conversations/{conversation_id}/messages").json()
        release.set()
        finished = original.result(timeout=5)
    assert (live.status_code, conflict.status_code, finished.status_code) == (200, 409, 200)
    assert conflict.json()["error"]["code"] == "turn_in_progress"
    assert len(history["items"]) == 1
