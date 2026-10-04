"""Strict assistant HTTP inputs and published OpenAPI response shapes."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from app.domain.validation import (
    normalized_message,
    normalized_title,
)


class CreateConversation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: StrictStr = Field(default="New conversation", min_length=1, max_length=100)

    @field_validator("title")
    @classmethod
    def title_nonblank(cls, value: str) -> str:
        return normalized_title(value)


class SendMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: StrictStr

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


class TraceStage(BaseModel):
    stage: str = "respond"
    summary: str = "Generated a reply with the configured model."


class TurnResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    turn_id: UUID
    conversation_id: UUID
    mode: Literal["provider_chat"] = "provider_chat"
    user_message: MessageResponse
    assistant_message: MessageResponse
    usage: UsageResponse
    trace: list[TraceStage] = Field(default_factory=lambda: [TraceStage()])


class AgentLimits(BaseModel):
    max_message_chars: int = 8000
    max_history_messages: int = 20
    max_output_tokens: int


class AgentMetadata(BaseModel):
    name: str = "Foundation Assistant"
    mode: Literal["provider_chat"] = "provider_chat"
    provider: str = "openai"
    model: str
    configured: bool
    retrieval_enabled: bool = False
    limits: AgentLimits
