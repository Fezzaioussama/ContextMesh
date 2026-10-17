"""Shared fixtures: captured flow log lines, and real PostgreSQL integration fixtures
isolated by canonical workspace identity."""

import logging
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from app.data.chat import ChatRepository
from app.data.db import provision_identity
from app.main import create_app
from app.services.models import Identity
from app.utils.config import Settings
from app.utils.logging import flow_logger
from app.wiring import build_worker
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from support import memory_services

BACKEND = Path(__file__).resolve().parents[1]


class FlowLines(logging.Handler):
    """Collects the flow lines logged while a test runs."""

    def __init__(self):
        super().__init__(logging.INFO)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def flow_lines():
    """The flow lines logged during the test, in order; the logger's level is restored."""
    collector, level = FlowLines(), flow_logger.level
    flow_logger.addHandler(collector)
    flow_logger.setLevel(logging.INFO)
    yield collector.lines
    flow_logger.removeHandler(collector)
    flow_logger.setLevel(level)


@pytest.fixture(scope="session")
def database_url():
    value = os.getenv("CONTEXTMESH_TEST_DATABASE_URL")
    if not value:
        pytest.skip("Set CONTEXTMESH_TEST_DATABASE_URL to run real PostgreSQL integration tests.")
    environment = dict(os.environ, CONTEXTMESH_DATABASE_URL=value)
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(BACKEND / "alembic.ini"), "upgrade", "head"],
        env=environment,
        check=True,
    )
    return value


@pytest.fixture
def engine(database_url):
    value = create_engine(database_url, pool_pre_ping=True)
    yield value
    value.dispose()


@pytest.fixture
def identity():
    return Identity(f"test-{uuid4()}", uuid4())


@pytest.fixture
def repository(engine, identity):
    provision_identity(engine, identity)
    return ChatRepository(engine, 90)


@pytest.fixture
def doubles():
    return memory_services()


@pytest.fixture
def services(doubles):
    return doubles[0]


@pytest.fixture
def reasoning(doubles):
    return doubles[1]


@pytest.fixture
def embeddings(doubles):
    return doubles[2]


@pytest.fixture
def web(doubles):
    return doubles[3]


@pytest.fixture
def settings(database_url, tmp_path):
    return Settings(
        database_url=database_url, openrouter_api_key="", blob_dir=tmp_path, _env_file=None
    )


@pytest.fixture
def client(settings, identity, services):
    with TestClient(create_app(settings, services, identity)) as value:
        yield value


@pytest.fixture
def worker(engine, settings, services):
    return build_worker(engine, settings, services)


@pytest.fixture
def conversation_id(client):
    response = client.post("/api/v1/assistant/conversations", json={})
    return response.json()["id"]
