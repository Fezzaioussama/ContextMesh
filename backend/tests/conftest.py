"""Real PostgreSQL integration fixtures, isolated by canonical workspace identity."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from app.bootstrap.api import create_app
from app.core.config import Settings
from app.core.security import Identity
from app.db.repositories.conversation_repository import (
    ConversationRepository,
)
from app.db.repositories.turn_repository import TurnRepository
from app.db.repositories.user_repository import DevelopmentIdentityRepository
from app.domain.models import ModelReply, Usage
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

BACKEND = Path(__file__).resolve().parents[1]


class FixtureModel:
    configured = True

    def __init__(self):
        self.calls = []
        self.failure = None

    def respond(self, history, message):
        self.calls.append((history, message))
        if self.failure is not None:
            raise self.failure
        return ModelReply("Fixture reply", Usage(12, 8))


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
def model():
    return FixtureModel()


@pytest.fixture
def settings(database_url):
    return Settings(database_url=database_url, api_key="", _env_file=None)


@pytest.fixture
def client(settings, identity, model):
    with TestClient(create_app(settings, model, identity)) as value:
        yield value


@pytest.fixture
def conversation_id(client):
    response = client.post("/api/v1/assistant/conversations", json={})
    return response.json()["id"]
