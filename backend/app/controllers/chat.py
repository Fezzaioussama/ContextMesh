"""Assistant endpoints and their published shapes. Synchronous endpoints run model and
SQL I/O in FastAPI's worker pool."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictStr, computed_field, field_validator

from app.controllers.http import Principal
from app.controllers.sources import LocatorResponse
from app.controllers.sources import Service as Sources
from app.services.chat import ConversationService
from app.services.models import Conversation, Message, Page, TurnResult
from app.services.validation import (
    MAX_MESSAGE_CHARS,
    MAX_SOURCE_FILTER,
    MEDIA_TYPES,
    normalized_message,
    normalized_title,
)


def conversation_service(request: Request) -> ConversationService:
    return request.app.state.conversations


Service = Annotated[ConversationService, Depends(conversation_service)]


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


class CitationResponse(BaseModel):
    """Display fields come from canonical records, never from model output."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    number: int
    source_id: UUID
    document_id: UUID
    document_version_id: UUID
    index_generation_id: UUID
    chunk_id: UUID
    title: str
    locator: LocatorResponse
    snippet: str
    source_url: str | None

    @computed_field
    @property
    def evidence_path(self) -> str:
        return (
            f"/api/v1/documents/{self.document_id}/versions/{self.document_version_id}"
            f"/evidence?chunk_id={self.chunk_id}"
        )


class ClaimResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    text: str
    citation_ids: list[str]


class AnswerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    status: Literal["direct", "answered", "partial", "insufficient_evidence", "withheld"]
    claims: list[ClaimResponse]
    citations: list[CitationResponse]
    gaps: list[str]


class TraceStage(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    stage: str
    summary: str


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
    max_message_chars: int = MAX_MESSAGE_CHARS
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
    supported_media_types: list[str] = sorted(MEDIA_TYPES)
    limits: AgentLimits


router = APIRouter(prefix="/api/v1/assistant")


@router.get("", response_model=AgentMetadata)
def metadata(service: Service, sources: Sources) -> AgentMetadata:
    current = service.metadata()
    policy = current.policy
    return AgentMetadata(
        provider=current.provider,
        model=current.model,
        embedding_model=current.embedding_model,
        configured=current.configured,
        limits=AgentLimits(
            max_history_messages=policy.history_messages,
            max_output_tokens=current.max_output_tokens,
            max_retrieval_rounds=policy.max_rounds,
            max_query_variants=policy.max_query_variants,
            max_repairs=policy.max_repairs,
            deadline_seconds=policy.deadline_seconds,
            max_upload_bytes=sources.max_upload_bytes,
        ),
    )


@router.post("/conversations", status_code=201, response_model=ConversationResponse)
def create_conversation(
    body: CreateConversation, service: Service, identity: Principal
) -> Conversation:
    return service.create(identity, body.title)


@router.get("/conversations", response_model=ConversationPage)
def list_conversations(
    service: Service,
    identity: Principal,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> Page[Conversation]:
    return service.conversations(identity, limit, cursor)


@router.get("/conversations/{conversation_id}/messages", response_model=MessagePage)
def list_messages(
    conversation_id: UUID,
    service: Service,
    identity: Principal,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> Page[Message]:
    return service.messages(identity, conversation_id, limit, cursor)


@router.post("/conversations/{conversation_id}/messages", response_model=TurnResponse)
def send_message(
    conversation_id: UUID,
    body: SendMessage,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
    service: Service,
    identity: Principal,
) -> TurnResult:
    scope = None if body.source_ids is None else tuple(body.source_ids)
    return service.send(identity, conversation_id, idempotency_key, body.message, scope)
