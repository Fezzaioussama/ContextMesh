"""Provider selection keeps credentials, destinations, and model IDs together."""

import pytest
from app.utils.config import Settings
from pydantic import ValidationError


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("openai", ("direct-key", "direct-model", "https://api.openai.com/v1")),
        ("openrouter", ("router-key", "vendor/router-model", "https://openrouter.ai/api/v1")),
    ],
)
def test_selection_uses_only_matching_credentials_model_and_destination(provider, expected):
    settings = Settings(
        _env_file=None,
        model_provider=provider,
        api_key="direct-key",
        model="direct-model",
        base_url="https://api.openai.com/v1",
        openrouter_api_key="router-key",
        openrouter_model="vendor/router-model",
        openrouter_base_url="https://openrouter.ai/api/v1",
    ).selected_model
    assert (settings.api_key.get_secret_value(), settings.model, settings.base_url) == expected
    assert settings.provider == provider


def test_openrouter_environment_names_configure_the_server(monkeypatch):
    monkeypatch.setenv("CONTEXTMESH_MODEL_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "vendor/router-model")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "http://fixture.test/v1")
    settings = Settings(_env_file=None).selected_model
    assert (settings.provider, settings.model, settings.base_url) == (
        "openrouter",
        "vendor/router-model",
        "http://fixture.test/v1",
    )
    assert settings.api_key.get_secret_value() == "router-key"


def test_openrouter_missing_key_does_not_use_openai_key():
    selected = Settings(
        _env_file=None, model_provider="openrouter", openrouter_api_key="", api_key="direct-key"
    ).selected_model
    assert selected.api_key.get_secret_value() == ""


def test_decision_configuration_uses_openrouter_credential_only():
    decision = Settings(
        _env_file=None,
        decision_enabled=True,
        api_key="direct-key-must-not-be-used",
        openrouter_api_key="router-key",
        decision_model="typesafe/fixture",
        decision_endpoint="http://fixture.test/decisions",
    ).selected_decision
    assert (
        decision.enabled,
        decision.api_key.get_secret_value(),
        decision.model,
        decision.endpoint,
    ) == (True, "router-key", "typesafe/fixture", "http://fixture.test/decisions")


def test_decision_configuration_never_falls_back_to_openai_key():
    decision = Settings(
        _env_file=None,
        decision_enabled=True,
        api_key="direct-key-must-not-be-used",
        openrouter_api_key="",
    ).selected_decision
    assert decision.api_key.get_secret_value() == ""


def test_decision_environment_names_configure_the_server(monkeypatch):
    monkeypatch.setenv("CONTEXTMESH_DECISION_ENABLED", "true")
    monkeypatch.setenv("CONTEXTMESH_DECISION_MODEL", "typesafe/environment-model")
    monkeypatch.setenv("CONTEXTMESH_DECISION_ENDPOINT", "http://fixture.test/decisions")
    monkeypatch.setenv("OPENROUTER_API_KEY", "environment-router-key")
    decision = Settings(_env_file=None).selected_decision
    assert (decision.enabled, decision.model, decision.endpoint) == (
        True,
        "typesafe/environment-model",
        "http://fixture.test/decisions",
    )
    assert decision.api_key.get_secret_value() == "environment-router-key"


def test_unknown_provider_is_rejected_before_startup():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, model_provider="unknown")


def test_selected_configuration_repr_masks_provider_secret():
    selected = Settings(
        _env_file=None, model_provider="openrouter", openrouter_api_key="private-router-key"
    ).selected_model
    assert "private-router-key" not in repr(selected)


@pytest.mark.parametrize(
    ("provider", "expected"),
    [("openai", "direct-embed"), ("openrouter", "vendor/router-embed")],
)
def test_embedding_model_follows_the_selected_provider(provider, expected):
    selected = Settings(
        _env_file=None,
        model_provider=provider,
        embedding_model="direct-embed",
        openrouter_embedding_model="vendor/router-embed",
    ).selected_model
    assert selected.embedding_model == expected


def test_relative_blob_directory_is_anchored_at_the_repository_root():
    settings = Settings(_env_file=None, blob_dir="relative/blobs")
    assert settings.blob_dir.is_absolute()
    assert settings.blob_dir.parts[-2:] == ("relative", "blobs")
