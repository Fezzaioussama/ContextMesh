"""Application-owned model ports; adapters supply bounded, stateless provider calls."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.services.rules.models import Usage


@dataclass(frozen=True)
class StructuredTask:
    name: str
    instructions: str
    content: str
    schema: Mapping[str, object]
    timeout_seconds: float


@dataclass(frozen=True)
class StructuredReply:
    data: Mapping[str, object]
    usage: Usage


class ReasoningModel(Protocol):
    @property
    def configured(self) -> bool: ...

    def complete(self, task: StructuredTask) -> StructuredReply: ...


class EmbeddingModel(Protocol):
    @property
    def identity(self) -> str: ...

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...
