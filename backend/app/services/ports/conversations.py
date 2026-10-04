"""Focused integration ports consumed by the assistant use case."""

from typing import Protocol
from uuid import UUID

from app.core.security import Identity
from app.domain.models import (
    Conversation,
    Execution,
    Message,
    ModelReply,
    Page,
    TurnResult,
)


class ConversationStore(Protocol):
    def create(self, identity: Identity, title: str) -> Conversation: ...

    def conversations(
        self, identity: Identity, limit: int, cursor: str | None
    ) -> Page[Conversation]: ...

    def messages(
        self, identity: Identity, conversation_id: UUID, limit: int, cursor: str | None
    ) -> Page[Message]: ...


class TurnStore(Protocol):
    def claim(
        self, identity: Identity, conversation_id: UUID, key: str, message: str
    ) -> Execution | TurnResult: ...

    def complete(
        self, identity: Identity, execution: Execution, reply: ModelReply
    ) -> TurnResult: ...

    def fail(self, identity: Identity, execution: Execution) -> None: ...
