"""Validated server-only configuration loaded from the repository environment."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ModelProviderSettings:
    provider: str
    api_key: SecretStr
    model: str
    base_url: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND.parent / ".env", extra="ignore", populate_by_name=True
    )
    database_url: str = Field(
        default="postgresql+psycopg://contextmesh:contextmesh@127.0.0.1:55432/contextmesh",
        validation_alias="CONTEXTMESH_DATABASE_URL",
    )
    api_key: SecretStr = Field(default=SecretStr(""), validation_alias="OPENAI_API_KEY")
    model: str = Field(default="gpt-4.1-mini", validation_alias="OPENAI_MODEL")
    base_url: str = Field(default="https://api.openai.com/v1", validation_alias="OPENAI_BASE_URL")
    model_provider: Literal["openai", "openrouter"] = Field(
        default="openai", validation_alias="CONTEXTMESH_MODEL_PROVIDER"
    )
    openrouter_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias="OPENROUTER_API_KEY"
    )
    openrouter_model: str = Field(
        default="openai/gpt-4.1-mini", validation_alias="OPENROUTER_MODEL"
    )
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", validation_alias="OPENROUTER_BASE_URL"
    )
    dev_subject: str = Field(
        default="local-user",
        min_length=1,
        max_length=200,
        validation_alias="CONTEXTMESH_DEV_SUBJECT",
    )
    dev_workspace_id: UUID = Field(
        default=UUID("00000000-0000-0000-0000-000000000001"),
        validation_alias="CONTEXTMESH_DEV_WORKSPACE_ID",
    )
    allowed_origins: list[str] = Field(
        default=["http://localhost:5173", "http://127.0.0.1:5173"],
        validation_alias="CONTEXTMESH_ALLOWED_ORIGINS",
    )
    provider_timeout_seconds: float = Field(
        default=45, gt=0, le=45, validation_alias="CONTEXTMESH_PROVIDER_TIMEOUT_SECONDS"
    )
    max_output_tokens: int = Field(
        default=1024, ge=1, le=4096, validation_alias="CONTEXTMESH_MAX_OUTPUT_TOKENS"
    )
    turn_lease_seconds: int = Field(
        default=90, gt=0, le=3600, validation_alias="CONTEXTMESH_TURN_LEASE_SECONDS"
    )

    @property
    def selected_model(self) -> ModelProviderSettings:
        if self.model_provider == "openrouter":
            return ModelProviderSettings(
                "openrouter",
                self.openrouter_api_key,
                self.openrouter_model,
                self.openrouter_base_url,
            )
        return ModelProviderSettings("openai", self.api_key, self.model, self.base_url)

    @field_validator("database_url")
    @classmethod
    def postgres_only(cls, value: str) -> str:
        if make_url(value).drivername != "postgresql+psycopg":
            raise ValueError("Use the postgresql+psycopg database driver.")
        return value

    @field_validator("dev_subject")
    @classmethod
    def nonblank_subject(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Development subject must be nonempty.")
        return value.strip()

    @model_validator(mode="after")
    def validated_lease(self):
        if self.turn_lease_seconds <= self.provider_timeout_seconds:
            raise ValueError("Turn lease must exceed the provider timeout.")
        return self
