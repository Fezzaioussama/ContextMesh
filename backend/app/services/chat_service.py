"""Public module facade; callers never resolve repositories or providers."""

from dataclasses import dataclass
from uuid import UUID

from app.core.security import Identity
from app.domain.models import Conversation, Message, Page, TurnResult
from app.domain.validation import checked_page, normalized_title
from app.services.assistant import Assistant
from app.services.ports.conversations import ConversationStore


@dataclass(frozen=True)
class AssistantMetadata:
    provider: str
    model: str
    configured: bool
    max_output_tokens: int


class ConversationService:
    def __init__(
        self,
        conversations: ConversationStore,
        assistant: Assistant,
        *,
        provider: str,
        model_name: str,
        max_output_tokens: int,
    ):
        self._conversations = conversations
        self._assistant = assistant
        self._provider = provider
        self._model_name = model_name
        self._max_output_tokens = max_output_tokens

    def metadata(self) -> AssistantMetadata:
        return AssistantMetadata(
            self._provider, self._model_name, self._assistant.configured, self._max_output_tokens
        )

    def create(self, identity: Identity, title: str) -> Conversation:
        return self._conversations.create(identity, normalized_title(title))

    def conversations(
        self, identity: Identity, limit: int, cursor: str | None
    ) -> Page[Conversation]:
        checked_page(limit, 50, cursor)
        return self._conversations.conversations(identity, limit, cursor)

    def messages(
        self, identity: Identity, conversation_id: UUID, limit: int, cursor: str | None
    ) -> Page[Message]:
        checked_page(limit, 100, cursor)
        return self._conversations.messages(identity, conversation_id, limit, cursor)

    def send(self, identity: Identity, conversation_id: UUID, key: str, message: str) -> TurnResult:
        return self._assistant.send(identity, conversation_id, key, message)
