"""OpenRouter settings keep credentials, destinations, and model IDs together."""

from app.utils.config import Settings


def test_openrouter_settings_keep_credential_model_and_destination_together():
    settings = Settings(
        _env_file=None,
        openrouter_api_key="router-key",
        openrouter_model="vendor/router-model",
        openrouter_embedding_model="vendor/router-embed",
        openrouter_rerank_model="vendor/router-rerank",
        openrouter_base_url="https://openrouter.ai/api/v1",
    ).selected_model
    assert (
        settings.provider,
        settings.api_key.get_secret_value(),
        settings.model,
        settings.embedding_model,
        settings.rerank_model,
        settings.base_url,
    ) == (
        "openrouter",
        "router-key",
        "vendor/router-model",
        "vendor/router-embed",
        "vendor/router-rerank",
        "https://openrouter.ai/api/v1",
    )


def test_default_chat_model_is_deepseek_flash(monkeypatch):
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    assert Settings(_env_file=None).selected_model.model == "deepseek/deepseek-v4.1-flash"


def test_default_retrieval_models_are_voyage(monkeypatch):
    monkeypatch.delenv("OPENROUTER_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_RERANK_MODEL", raising=False)
    selected = Settings(_env_file=None).selected_model
    assert (selected.embedding_model, selected.rerank_model) == (
        "voyageai/voyage-4-large",
        "voyageai/rerank-3",
    )


def test_openrouter_environment_names_configure_the_server(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "vendor/router-model")
    monkeypatch.setenv("OPENROUTER_RERANK_MODEL", "vendor/router-rerank")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "http://fixture.test/v1")
    settings = Settings(_env_file=None).selected_model
    assert (settings.provider, settings.model, settings.rerank_model, settings.base_url) == (
        "openrouter",
        "vendor/router-model",
        "vendor/router-rerank",
        "http://fixture.test/v1",
    )
    assert settings.api_key.get_secret_value() == "router-key"


def test_openai_environment_names_are_ignored(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "direct-key")
    monkeypatch.setenv("OPENAI_MODEL", "direct-model")
    selected = Settings(_env_file=None).selected_model
    assert (selected.api_key.get_secret_value(), selected.model) == (
        "",
        "deepseek/deepseek-v4.1-flash",
    )


def test_decision_configuration_uses_openrouter_credential_only():
    decision = Settings(
        _env_file=None,
        decision_enabled=True,
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


def test_selected_configuration_repr_masks_provider_secret():
    selected = Settings(_env_file=None, openrouter_api_key="private-router-key").selected_model
    assert "private-router-key" not in repr(selected)


def test_relative_blob_directory_is_anchored_at_the_repository_root():
    settings = Settings(_env_file=None, blob_dir="relative/blobs")
    assert settings.blob_dir.is_absolute()
    assert settings.blob_dir.parts[-2:] == ("relative", "blobs")
