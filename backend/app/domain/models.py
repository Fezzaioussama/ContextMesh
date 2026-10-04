"""Transport-independent values for the initial, non-retrieval assistant."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID


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


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class ModelReply:
    content: str
    usage: Usage


@dataclass(frozen=True)
class TurnResult:
    turn_id: UUID
    conversation_id: UUID
    user_message: Message
    assistant_message: Message
    usage: Usage


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
