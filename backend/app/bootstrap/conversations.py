"""Wire the conversation module's explicit, consumer-focused dependencies."""

from sqlalchemy import Engine

from app.ai.llm.factory import create_chat_model
from app.ai.llm.openai import OpenAIChatModel
from app.core.config import Settings
from app.db.repositories.conversation_repository import (
    ConversationRepository,
)
from app.db.repositories.turn_repository import TurnRepository
from app.services.assistant import Assistant
from app.services.chat_service import ConversationService
from app.services.ports.models import ChatModel


def create_model(config: Settings) -> OpenAIChatModel:
    provider = config.selected_model
    return create_chat_model(
        api_key=provider.api_key.get_secret_value(),
        model=provider.model,
        base_url=provider.base_url,
        timeout=config.provider_timeout_seconds,
        max_output_tokens=config.max_output_tokens,
    )


def conversation_service(engine: Engine, config: Settings, model: ChatModel) -> ConversationService:
    provider = config.selected_model
    return ConversationService(
        ConversationRepository(engine),
        Assistant(TurnRepository(engine, config.turn_lease_seconds), model),
        provider=provider.provider,
        model_name=provider.model,
        max_output_tokens=config.max_output_tokens,
    )
