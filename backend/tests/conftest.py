"""Real PostgreSQL integration fixtures, isolated by canonical workspace identity."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from app.bootstrap.api import create_app
from app.bootstrap.knowledge import ingestion_worker
from app.core.config import Settings
from app.core.security import Identity
from app.db.repositories.conversation_repository import (
    ConversationRepository,
)
from app.db.repositories.turn_repository import TurnRepository
from app.db.repositories.user_repository import DevelopmentIdentityRepository
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from support import memory_services

BACKEND = Path(__file__).resolve().parents[1]


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
    DevelopmentIdentityRepository(engine).provision(identity)
    return ConversationRepository(engine)


@pytest.fixture
def turn_repository(engine, repository):
    return TurnRepository(engine, 90)


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
def settings(database_url, tmp_path):
    return Settings(database_url=database_url, api_key="", blob_dir=tmp_path, _env_file=None)


@pytest.fixture
def client(settings, identity, services):
    with TestClient(create_app(settings, services, identity)) as value:
        yield value


@pytest.fixture
def worker(engine, settings, services):
    return ingestion_worker(engine, settings, services)


@pytest.fixture
def conversation_id(client):
    response = client.post("/api/v1/assistant/conversations", json={})
    return response.json()["id"]
