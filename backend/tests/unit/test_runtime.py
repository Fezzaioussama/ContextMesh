"""Owned resources close on shutdown and failure; injected services stay caller-owned. A
hosted app accepts platform database URLs and serves the built frontend beside the API."""

from contextlib import ExitStack
from unittest.mock import Mock

import pytest
from app import main, wiring
from app.utils.config import Settings
from app.wiring import ExternalServices
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError


@pytest.fixture
def resources(monkeypatch):
    engine = Mock(spec=Engine)
    model = Mock()
    services = ExternalServices(Mock(), Mock(), Mock(), Mock())

    def create(config, stack):
        stack.callback(model.close)
        return services

    factory = Mock(side_effect=create)
    monkeypatch.setattr(wiring, "create_database_engine", Mock(return_value=engine))
    monkeypatch.setattr(wiring, "create_services", factory)
    monkeypatch.setattr(wiring, "provision_identity", Mock())
    settings = Settings(_env_file=None, openrouter_api_key="")
    return settings, engine, model, factory


def test_owned_provider_and_database_close_after_shutdown(resources):
    settings, engine, model, _ = resources
    with TestClient(main.create_app(settings)) as client:
        assert client.get("/health/live").status_code == 200
    model.close.assert_called_once_with()
    engine.dispose.assert_called_once_with()


def test_injected_provider_lifetime_belongs_to_caller(resources):
    settings, engine, model, factory = resources
    with TestClient(main.create_app(settings, ExternalServices(Mock(), Mock(), Mock(), Mock()))):
        pass
    factory.assert_not_called()
    model.close.assert_not_called()
    engine.dispose.assert_called_once_with()


def test_startup_failure_releases_owned_resources(resources, monkeypatch):
    settings, engine, model, _ = resources
    provision = Mock(side_effect=RuntimeError("startup failure"))
    monkeypatch.setattr(wiring, "provision_identity", provision)
    with (
        pytest.raises(RuntimeError, match="startup failure"),
        TestClient(main.create_app(settings)),
    ):
        pass
    model.close.assert_called_once_with()
    engine.dispose.assert_called_once_with()


def test_database_startup_failure_preserves_process_liveness(resources, monkeypatch):
    settings, engine, model, _ = resources
    provision = Mock(side_effect=SQLAlchemyError("private connection details"))
    monkeypatch.setattr(wiring, "provision_identity", provision)
    with TestClient(main.create_app(settings)) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
    model.close.assert_called_once_with()
    engine.dispose.assert_called_once_with()


def test_provider_cleanup_failure_still_disposes_database(resources):
    settings, engine, model, _ = resources
    model.close.side_effect = RuntimeError("close failure")
    with pytest.raises(RuntimeError, match="close failure"), TestClient(main.create_app(settings)):
        pass
    engine.dispose.assert_called_once_with()


def test_provider_construction_failure_disposes_database(resources):
    settings, engine, _, factory = resources
    factory.side_effect = ValueError("invalid provider configuration")
    with pytest.raises(ValueError, match="invalid provider configuration"):
        main.create_app(settings)
    engine.dispose.assert_called_once_with()


def test_http_composition_failure_releases_owned_resources(resources, monkeypatch):
    settings, engine, model, _ = resources
    monkeypatch.setattr(main, "compose_app", Mock(side_effect=RuntimeError("composition failure")))
    with pytest.raises(RuntimeError, match="composition failure"):
        main.create_app(settings)
    model.close.assert_called_once_with()
    engine.dispose.assert_called_once_with()


def test_production_external_services_own_decision_adapter(monkeypatch):
    reasoning = Mock()
    embeddings = Mock(identity="fixture-embedding")
    decisions = Mock()
    vector_client = Mock()
    vectors = Mock()
    fetcher = Mock()
    monkeypatch.setattr(wiring, "OpenRouterReasoningModel", Mock(return_value=reasoning))
    monkeypatch.setattr(wiring, "OpenRouterEmbeddingModel", Mock(return_value=embeddings))
    monkeypatch.setattr(wiring, "OpenRouterDecisionModel", Mock(return_value=decisions))
    monkeypatch.setattr(wiring, "QdrantClient", Mock(return_value=vector_client))
    monkeypatch.setattr(wiring, "QdrantVectorIndex", Mock(return_value=vectors))
    monkeypatch.setattr(wiring, "SafeHttpFetcher", Mock(return_value=fetcher))
    settings = Settings(
        _env_file=None, decision_enabled=True, openrouter_api_key="router-fixture-key"
    )
    with ExitStack() as resources:
        external = wiring.create_services(settings, resources)
        assert external.decisions is decisions
        decisions.close.assert_not_called()
    decisions.close.assert_called_once_with()


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://user:p%40ss@db:5432/app", "postgresql+psycopg://user:p%40ss@db:5432/app"),
        ("postgres://user:pass@db:5432/app", "postgresql+psycopg://user:pass@db:5432/app"),
        ("postgresql+psycopg://user:pass@db/app", "postgresql+psycopg://user:pass@db/app"),
    ],
    ids=["railway", "legacy-scheme", "already-psycopg"],
)
def test_platform_database_urls_get_the_psycopg_driver(url, expected):
    assert Settings(_env_file=None, database_url=url).database_url == expected


def test_other_database_drivers_are_refused():
    with pytest.raises(ValueError, match="postgresql\\+psycopg driver"):
        Settings(_env_file=None, database_url="mysql://user:pass@db/app")


def test_the_built_frontend_is_served_beside_the_api(resources, tmp_path):
    (tmp_path / "index.html").write_text("<div id=root></div>")
    hosted = Settings(_env_file=None, openrouter_api_key="", frontend_dir=tmp_path)
    with TestClient(main.create_app(hosted)) as client:
        page, health = client.get("/"), client.get("/health/live")
        missing = client.get("/api/v1/missing")
    assert (page.status_code, page.text) == (200, "<div id=root></div>")
    assert health.json() == {"status": "ok"}
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "http_error")


def test_an_empty_frontend_dir_serves_no_frontend(resources):
    settings = Settings(_env_file=None, openrouter_api_key="", frontend_dir="")
    with TestClient(main.create_app(settings)) as client:
        assert (settings.frontend_dir, client.get("/").status_code) == (None, 404)
