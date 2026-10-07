"""Focused persistence ports consumed by the conversation and assistant use cases."""

from typing import Protocol
from uuid import UUID

from app.core.security import Identity
from app.domain.models import (
    AgentOutcome,
    Conversation,
    Execution,
    Message,
    Page,
    TurnInput,
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
    def replay(
        self, identity: Identity, conversation_id: UUID, key: str, turn: TurnInput
    ) -> TurnResult | None: ...

    def claim(
        self, identity: Identity, conversation_id: UUID, key: str, turn: TurnInput
    ) -> Execution | TurnResult: ...

    def complete(
        self, identity: Identity, execution: Execution, outcome: AgentOutcome
    ) -> TurnResult: ...

    def fail(self, identity: Identity, execution: Execution, code: str) -> None: ...
