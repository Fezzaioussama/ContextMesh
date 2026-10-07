"""HTTP dependency bindings keep concurrent application instances isolated."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from unittest.mock import Mock
from uuid import uuid4

import pytest
from app.controllers import deps
from app.controllers.deps import ChatDependencies, HealthDependencies, SourceDependencies
from app.controllers.errors import install_error_handlers
from app.controllers.health import health_router
from app.controllers.router import api_router
from app.services.agent.policy import AgentPolicy
from app.services.chat_service import AssistantMetadata, ConversationService
from app.services.health_service import HealthService
from app.services.ports.health import DependencyHealth
from app.services.rules.models import Conversation
from app.services.sources import SourceService
from app.utils.security import Identity
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = "/api/v1/assistant"


def service_for(name: str) -> Mock:
    service = Mock(spec=ConversationService)
    service.metadata.return_value = AssistantMetadata(
        "openai", name, "embed", True, 1024, AgentPolicy()
    )
    now = datetime.now(UTC)
    service.create.return_value = Conversation(uuid4(), name, now, now)
    return service


def client_for(service: ConversationService, identity: Identity, database: DependencyHealth):
    app = FastAPI()
    install_error_handlers(app)
    chat = ChatDependencies(service, identity)
    health = HealthDependencies(HealthService(database))
    sources = SourceDependencies(Mock(spec=SourceService, max_upload_bytes=1000))
    app.dependency_overrides.update(
        {
            deps.conversation_service: chat.conversation_service,
            deps.principal: chat.principal,
            deps.source_service: sources.source_service,
            deps.health_service: health.health_service,
        }
    )
    app.include_router(api_router())
    app.include_router(health_router())
    return TestClient(app)


@pytest.fixture
def isolated_apps():
    first_identity = Identity("first-subject", uuid4())
    second_identity = Identity("second-subject", uuid4())
    first_service = service_for("first-model")
    second_service = service_for("second-model")
    first_database = Mock(spec=DependencyHealth, ready=Mock(return_value=True))
    second_database = Mock(spec=DependencyHealth, ready=Mock(return_value=False))
    with (
        client_for(first_service, first_identity, first_database) as first_client,
        client_for(second_service, second_identity, second_database) as second_client,
    ):
        yield (
            (first_client, first_service, first_identity),
            (second_client, second_service, second_identity),
        )


def overlapping_create(service: Mock, barrier: Barrier):
    def create(identity: Identity, title: str) -> Conversation:
        barrier.wait(timeout=5)
        return service.create.return_value

    return create


def test_concurrent_apps_use_their_own_service_and_server_identity(isolated_apps):
    (
        (first_client, first_service, first_identity),
        (
            second_client,
            second_service,
            second_identity,
        ),
    ) = isolated_apps
    barrier = Barrier(2)
    first_service.create.side_effect = overlapping_create(first_service, barrier)
    second_service.create.side_effect = overlapping_create(second_service, barrier)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            first_client.post, f"{ROOT}/conversations", json={"title": "First title"}
        )
        second = executor.submit(
            second_client.post, f"{ROOT}/conversations", json={"title": "Second title"}
        )
        responses = first.result(timeout=10), second.result(timeout=10)
    assert (responses[0].status_code, responses[1].status_code) == (201, 201)
    first_service.create.assert_called_once_with(first_identity, "First title")
    second_service.create.assert_called_once_with(second_identity, "Second title")


def test_metadata_uses_each_apps_provider_service(isolated_apps):
    (first_client, _, _), (second_client, _, _) = isolated_apps
    assert (first_client.get(ROOT).json()["model"], second_client.get(ROOT).json()["model"]) == (
        "first-model",
        "second-model",
    )


def test_client_headers_cannot_replace_the_bound_server_identity(isolated_apps):
    (first_client, first_service, first_identity), (_, _, second_identity) = isolated_apps
    response = first_client.post(
        f"{ROOT}/conversations",
        json={},
        headers={
            "X-User-ID": second_identity.subject,
            "X-Workspace-ID": str(second_identity.workspace_id),
        },
    )
    assert response.status_code == 201
    first_service.create.assert_called_once_with(first_identity, "New conversation")


def test_readiness_uses_each_apps_database_dependency(isolated_apps):
    (first_client, _, _), (second_client, _, _) = isolated_apps
    assert (
        first_client.get("/health/ready").status_code,
        second_client.get("/health/ready").status_code,
    ) == (
        200,
        503,
    )
    assert second_client.get("/health/live").json() == {"status": "ok"}
