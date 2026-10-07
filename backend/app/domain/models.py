"""Transport-independent conversation values for the grounded assistant."""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Literal
from uuid import UUID

from app.domain.answers import Answer, TraceStage


@dataclass(frozen=True)
class Conversation:
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Message:
    id: UUID
    turn_id: UUID
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime
    answer: Answer | None = None
    trace: tuple[TraceStage, ...] = ()


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens
        )


@dataclass(frozen=True)
class TurnInput:
    message: str
    source_ids: tuple[UUID, ...] | None = None

    @property
    def fingerprint(self) -> str:
        """Unfiltered turns keep the original message-only hash for replay compatibility."""
        payload = self.message
        if self.source_ids is not None:
            scope = ",".join(sorted(str(item) for item in self.source_ids))
            payload = f"{self.message}\x00{scope}"
        return sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class AgentOutcome:
    """`consulted` lists every document whose passages a model saw for this answer."""

    answer: Answer
    trace: tuple[TraceStage, ...]
    usage: Usage
    consulted: tuple[UUID, ...] = ()


@dataclass(frozen=True)
class TurnResult:
    turn_id: UUID
    conversation_id: UUID
    user_message: Message
    assistant_message: Message
    usage: Usage
    trace: tuple[TraceStage, ...] = ()


@dataclass(frozen=True)
class Execution:
    turn_id: UUID
    conversation_id: UUID
    token: UUID
    user_message: Message
    history: tuple[Message, ...]


@dataclass(frozen=True)
class Page[T]:
    items: tuple[T, ...]
    next_cursor: str | None
