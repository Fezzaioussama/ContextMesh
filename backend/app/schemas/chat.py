"""Strict assistant HTTP inputs and published OpenAPI response shapes."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from app.domain.validation import (
    MAX_SOURCE_FILTER,
    normalized_message,
    normalized_title,
)
from app.schemas.answers import AnswerResponse, TraceStage


class CreateConversation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: StrictStr = Field(default="New conversation", min_length=1, max_length=100)

    @field_validator("title")
    @classmethod
    def title_nonblank(cls, value: str) -> str:
        return normalized_title(value)


class SendMessage(BaseModel):
    """An omitted source filter means every eligible source; an empty list is invalid."""

    model_config = ConfigDict(extra="forbid")
    message: StrictStr
    source_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=MAX_SOURCE_FILTER)

    @field_validator("message")
    @classmethod
    def valid_message(cls, value: str) -> str:
        return normalized_message(value)


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    turn_id: UUID
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime
    answer: AnswerResponse | None
    trace: list[TraceStage]


class ConversationPage(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    items: list[ConversationResponse]
    next_cursor: str | None


class MessagePage(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    items: list[MessageResponse]
    next_cursor: str | None


class UsageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    input_tokens: int
    output_tokens: int


class TurnResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    turn_id: UUID
    conversation_id: UUID
    mode: Literal["agentic_rag"] = "agentic_rag"
    user_message: MessageResponse
    assistant_message: MessageResponse
    usage: UsageResponse
    trace: list[TraceStage]


class AgentLimits(BaseModel):
    max_message_chars: int = 8000
    max_history_messages: int
    max_output_tokens: int
    max_retrieval_rounds: int
    max_query_variants: int
    max_repairs: int
    deadline_seconds: float
    max_upload_bytes: int


class AgentMetadata(BaseModel):
    name: str = "ContextMesh Agent"
    mode: Literal["agentic_rag"] = "agentic_rag"
    provider: str = "openai"
    model: str
    embedding_model: str
    configured: bool
    retrieval_enabled: bool = True
    supported_media_types: list[str] = [".md", ".markdown", ".txt"]
    limits: AgentLimits
