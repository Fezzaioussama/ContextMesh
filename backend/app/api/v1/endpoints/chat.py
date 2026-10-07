"""Synchronous endpoints run provider and SQL I/O in FastAPI's worker pool."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query

from app.api.deps import conversation_service, principal, source_service
from app.core.security import Identity
from app.domain.models import Conversation, Message, Page, TurnResult
from app.schemas.chat import (
    AgentLimits,
    AgentMetadata,
    ConversationPage,
    ConversationResponse,
    CreateConversation,
    MessagePage,
    SendMessage,
    TurnResponse,
)
from app.services.chat_service import ConversationService
from app.services.sources import SourceService


def metadata(
    service: Annotated[ConversationService, Depends(conversation_service)],
    uploads: Annotated[SourceService, Depends(source_service)],
) -> AgentMetadata:
    metadata = service.metadata()
    policy = metadata.policy
    return AgentMetadata(
        provider=metadata.provider,
        model=metadata.model,
        embedding_model=metadata.embedding_model,
        configured=metadata.configured,
        limits=AgentLimits(
            max_history_messages=policy.history_messages,
            max_output_tokens=metadata.max_output_tokens,
            max_retrieval_rounds=policy.max_rounds,
            max_query_variants=policy.max_query_variants,
            max_repairs=policy.max_repairs,
            deadline_seconds=policy.deadline_seconds,
            max_upload_bytes=uploads.max_upload_bytes,
        ),
    )


def create_conversation(
    body: CreateConversation,
    service: Annotated[ConversationService, Depends(conversation_service)],
    identity: Annotated[Identity, Depends(principal)],
) -> Conversation:
    return service.create(identity, body.title)


def list_conversations(
    service: Annotated[ConversationService, Depends(conversation_service)],
    identity: Annotated[Identity, Depends(principal)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> Page[Conversation]:
    return service.conversations(identity, limit, cursor)


def list_messages(
    conversation_id: UUID,
    service: Annotated[ConversationService, Depends(conversation_service)],
    identity: Annotated[Identity, Depends(principal)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> Page[Message]:
    return service.messages(identity, conversation_id, limit, cursor)


def send_message(
    conversation_id: UUID,
    body: SendMessage,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
    service: Annotated[ConversationService, Depends(conversation_service)],
    identity: Annotated[Identity, Depends(principal)],
) -> TurnResult:
    scope = None if body.source_ids is None else tuple(body.source_ids)
    return service.send(identity, conversation_id, idempotency_key, body.message, scope)


def assistant_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/assistant")
    router.add_api_route("", metadata, response_model=AgentMetadata)
    router.add_api_route(
        "/conversations",
        create_conversation,
        methods=["POST"],
        status_code=201,
        response_model=ConversationResponse,
    )
    router.add_api_route("/conversations", list_conversations, response_model=ConversationPage)
    router.add_api_route(
        "/conversations/{conversation_id}/messages",
        list_messages,
        response_model=MessagePage,
    )
    router.add_api_route(
        "/conversations/{conversation_id}/messages",
        send_message,
        methods=["POST"],
        response_model=TurnResponse,
    )
    return router
