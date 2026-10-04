"""Dependency failures keep process liveness and return safe HTTP errors."""

from app.bootstrap.api import create_app
from app.core.config import Settings
from fastapi.testclient import TestClient


def test_unavailable_database_keeps_liveness_but_readiness_and_writes_fail_safely():
    settings = Settings(
        database_url="postgresql+psycopg://secret:private@127.0.0.1:1/missing",
        api_key="",
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        live = client.get("/health/live")
        ready = client.get("/health/ready")
        create = client.post("/api/v1/assistant/conversations", json={})
    assert (live.status_code, ready.status_code, create.status_code) == (200, 503, 503)
    assert ready.json()["error"]["code"] == "database_unavailable"
    assert create.json()["error"]["message"] == "The database is unavailable."
