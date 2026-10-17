"""Validated server-only configuration loaded from the repository environment."""

from dataclasses import dataclass
from pathlib import Path
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
    embedding_model: str
    rerank_model: str
    base_url: str


@dataclass(frozen=True)
class DecisionProviderSettings:
    enabled: bool
    api_key: SecretStr
    model: str
    endpoint: str


@dataclass(frozen=True)
class TracingSettings:
    enabled: bool
    api_key: SecretStr
    project: str
    endpoint: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND.parent / ".env", extra="ignore", populate_by_name=True
    )
    database_url: str = Field(
        default="postgresql+psycopg://contextmesh:contextmesh@127.0.0.1:55432/contextmesh",
        validation_alias="CONTEXTMESH_DATABASE_URL",
    )
    openrouter_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias="OPENROUTER_API_KEY"
    )
    openrouter_model: str = Field(
        default="deepseek/deepseek-v4.1-flash", validation_alias="OPENROUTER_MODEL"
    )
    openrouter_embedding_model: str = Field(
        default="voyageai/voyage-4-large", validation_alias="OPENROUTER_EMBEDDING_MODEL"
    )
    # Empty turns model reranking off; retrieval then ranks by query-term coverage.
    openrouter_rerank_model: str = Field(
        default="voyageai/rerank-3", validation_alias="OPENROUTER_RERANK_MODEL"
    )
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", validation_alias="OPENROUTER_BASE_URL"
    )
    # The offline evaluation's judge; empty reuses OPENROUTER_MODEL (see `judge_model`).
    openrouter_judge_model: str = Field(default="", validation_alias="OPENROUTER_JUDGE_MODEL")
    decision_enabled: bool = Field(default=False, validation_alias="CONTEXTMESH_DECISION_ENABLED")
    decision_model: str = Field(
        default="typesafe/jev-1.13", validation_alias="CONTEXTMESH_DECISION_MODEL"
    )
    decision_endpoint: str = Field(
        default="https://openrouter.ai/api/alpha/decisions",
        validation_alias="CONTEXTMESH_DECISION_ENDPOINT",
    )
    langsmith_tracing: bool = Field(default=False, validation_alias="LANGSMITH_TRACING")
    langsmith_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias="LANGSMITH_API_KEY"
    )
    langsmith_project: str = Field(
        default="contextmesh", min_length=1, validation_alias="LANGSMITH_PROJECT"
    )
    langsmith_endpoint: str = Field(
        default="https://api.smith.langchain.com", validation_alias="LANGSMITH_ENDPOINT"
    )
    qdrant_url: str = Field(
        default="http://127.0.0.1:6333", validation_alias="CONTEXTMESH_QDRANT_URL"
    )
    blob_dir: Path = Field(
        default=BACKEND.parent / ".data" / "blobs", validation_alias="CONTEXTMESH_BLOB_DIR"
    )
    max_upload_bytes: int = Field(
        default=20_000_000, ge=1, le=50_000_000, validation_alias="CONTEXTMESH_MAX_UPLOAD_BYTES"
    )
    crawl_max_pages: int = Field(
        default=50, ge=1, le=500, validation_alias="CONTEXTMESH_CRAWL_MAX_PAGES"
    )
    crawl_max_depth: int = Field(
        default=2, ge=0, le=5, validation_alias="CONTEXTMESH_CRAWL_MAX_DEPTH"
    )
    web_timeout_seconds: float = Field(
        default=10, gt=0, le=30, validation_alias="CONTEXTMESH_WEB_TIMEOUT_SECONDS"
    )
    web_max_page_bytes: int = Field(
        default=5_000_000, ge=1, le=20_000_000, validation_alias="CONTEXTMESH_WEB_MAX_PAGE_BYTES"
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
        default=8192, ge=1, le=16384, validation_alias="CONTEXTMESH_MAX_OUTPUT_TOKENS"
    )
    agent_deadline_seconds: float = Field(
        default=75, ge=10, le=300, validation_alias="CONTEXTMESH_AGENT_DEADLINE_SECONDS"
    )
    turn_lease_seconds: int = Field(
        default=90, gt=0, le=3600, validation_alias="CONTEXTMESH_TURN_LEASE_SECONDS"
    )
    job_lease_seconds: int = Field(
        default=90, ge=30, le=3600, validation_alias="CONTEXTMESH_JOB_LEASE_SECONDS"
    )

    @property
    def selected_model(self) -> ModelProviderSettings:
        """OpenRouter is the only model provider, for chat, embeddings, and reranking."""
        return ModelProviderSettings(
            "openrouter",
            self.openrouter_api_key,
            self.openrouter_model,
            self.openrouter_embedding_model,
            self.openrouter_rerank_model,
            self.openrouter_base_url,
        )

    @property
    def selected_decision(self) -> DecisionProviderSettings:
        """Jev shares the OpenRouter credential but stays off unless enabled."""
        return DecisionProviderSettings(
            self.decision_enabled,
            self.openrouter_api_key,
            self.decision_model,
            self.decision_endpoint,
        )

    @property
    def judge_model(self) -> str:
        """The evaluation judge's model, or the chat model when none is set.

        A judge that grades its own model's answers is biased, so the evaluation CLI warns
        when the two are the same.
        """
        return self.openrouter_judge_model.strip() or self.openrouter_model

    @property
    def selected_tracing(self) -> TracingSettings:
        """LangSmith receives prompts, passages, and answers, so it is strictly opt-in."""
        return TracingSettings(
            self.langsmith_tracing,
            self.langsmith_api_key,
            self.langsmith_project,
            self.langsmith_endpoint,
        )

    @field_validator("database_url")
    @classmethod
    def postgres_only(cls, value: str) -> str:
        if make_url(value).drivername != "postgresql+psycopg":
            raise ValueError("Use the postgresql+psycopg database driver.")
        return value

    @field_validator("blob_dir")
    @classmethod
    def anchored_blob_dir(cls, value: Path) -> Path:
        """API and worker must agree on one directory regardless of their working dirs."""
        if value.is_absolute():
            return value
        return BACKEND.parent / value

    @field_validator("dev_subject")
    @classmethod
    def nonblank_subject(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Development subject must be nonempty.")
        return value.strip()

    @model_validator(mode="after")
    def validated_tracing(self):
        if self.langsmith_tracing and not self.langsmith_api_key.get_secret_value().strip():
            raise ValueError("LANGSMITH_TRACING=true requires LANGSMITH_API_KEY.")
        return self

    @model_validator(mode="after")
    def validated_turn_lease(self):
        if self.turn_lease_seconds <= self.provider_timeout_seconds:
            raise ValueError("Turn lease must exceed the provider timeout.")
        if self.turn_lease_seconds <= self.agent_deadline_seconds:
            raise ValueError("Turn lease must exceed the agent deadline.")
        return self

    @model_validator(mode="after")
    def validated_job_lease(self):
        """A worker heartbeats between pages; the fetches before the first must fit."""
        if self.job_lease_seconds <= self.provider_timeout_seconds:
            raise ValueError("Job lease must exceed the provider timeout.")
        if self.job_lease_seconds <= 6 * self.web_timeout_seconds:
            raise ValueError(
                "Job lease must exceed three page fetches (six times the web timeout): "
                "robots.txt, the start page, and robots.txt again after a redirect."
            )
        return self
