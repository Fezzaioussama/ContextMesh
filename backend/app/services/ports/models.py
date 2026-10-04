"""Application-owned model port; implementations supply bounded, stateless replies."""

from typing import Protocol

from app.domain.models import Message, ModelReply


class ChatModel(Protocol):
    @property
    def configured(self) -> bool: ...

    def respond(self, history: tuple[Message, ...], message: str) -> ModelReply: ...
