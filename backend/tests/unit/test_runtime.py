"""Owned resources close on shutdown and failure; injected services stay caller-owned."""

from unittest.mock import Mock

import pytest
from app.bootstrap import api, runtime
from app.bootstrap.services import ExternalServices
from app.core.config import Settings
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
    monkeypatch.setattr(runtime, "create_database_engine", Mock(return_value=engine))
    monkeypatch.setattr(runtime, "create_services", factory)
    monkeypatch.setattr(runtime.DevelopmentIdentityRepository, "provision", Mock())
    settings = Settings(_env_file=None, api_key="")
    return settings, engine, model, factory


def test_owned_provider_and_database_close_after_shutdown(resources):
    settings, engine, model, _ = resources
    with TestClient(api.create_app(settings)) as client:
        assert client.get("/health/live").status_code == 200
    model.close.assert_called_once_with()
    engine.dispose.assert_called_once_with()


def test_injected_provider_lifetime_belongs_to_caller(resources):
    settings, engine, model, factory = resources
    with TestClient(api.create_app(settings, ExternalServices(Mock(), Mock(), Mock(), Mock()))):
        pass
    factory.assert_not_called()
    model.close.assert_not_called()
    engine.dispose.assert_called_once_with()


def test_startup_failure_releases_owned_resources(resources, monkeypatch):
    settings, engine, model, _ = resources
    provision = Mock(side_effect=RuntimeError("startup failure"))
    monkeypatch.setattr(runtime.DevelopmentIdentityRepository, "provision", provision)
    with pytest.raises(RuntimeError, match="startup failure"), TestClient(api.create_app(settings)):
        pass
    model.close.assert_called_once_with()
    engine.dispose.assert_called_once_with()


def test_database_startup_failure_preserves_process_liveness(resources, monkeypatch):
    settings, engine, model, _ = resources
    provision = Mock(side_effect=SQLAlchemyError("private connection details"))
    monkeypatch.setattr(runtime.DevelopmentIdentityRepository, "provision", provision)
    with TestClient(api.create_app(settings)) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
    model.close.assert_called_once_with()
    engine.dispose.assert_called_once_with()


def test_provider_cleanup_failure_still_disposes_database(resources):
    settings, engine, model, _ = resources
    model.close.side_effect = RuntimeError("close failure")
    with pytest.raises(RuntimeError, match="close failure"), TestClient(api.create_app(settings)):
        pass
    engine.dispose.assert_called_once_with()


def test_provider_construction_failure_disposes_database(resources):
    settings, engine, _, factory = resources
    factory.side_effect = ValueError("invalid provider configuration")
    with pytest.raises(ValueError, match="invalid provider configuration"):
        api.create_app(settings)
    engine.dispose.assert_called_once_with()


def test_http_composition_failure_releases_owned_resources(resources, monkeypatch):
    settings, engine, model, _ = resources
    monkeypatch.setattr(api, "compose_app", Mock(side_effect=RuntimeError("composition failure")))
    with pytest.raises(RuntimeError, match="composition failure"):
        api.create_app(settings)
    model.close.assert_called_once_with()
    engine.dispose.assert_called_once_with()
