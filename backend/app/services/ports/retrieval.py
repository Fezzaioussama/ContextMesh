"""Query-side ports; only canonical hydration may turn candidate IDs into text."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.services.rules.knowledge import CatalogSource, Evidence, Passage
from app.services.rules.models import AgentOutcome, Message
from app.utils.security import Identity


@dataclass(frozen=True)
class RetrievalResult:
    evidence: tuple[Evidence, ...]
    candidates: int
    vector_available: bool


class SourceCatalog(Protocol):
    def eligible(
        self, identity: Identity, source_ids: tuple[UUID, ...] | None
    ) -> tuple[CatalogSource, ...]: ...


class VectorSearch(Protocol):
    def search(
        self,
        vector: tuple[float, ...],
        workspace_id: UUID,
        source_ids: tuple[UUID, ...],
        limit: int,
    ) -> tuple[UUID, ...]: ...


class LexicalSearch(Protocol):
    def search(
        self, identity: Identity, source_ids: tuple[UUID, ...], query: str, limit: int
    ) -> tuple[UUID, ...]: ...


class PassageStore(Protocol):
    def hydrate(
        self, identity: Identity, source_ids: tuple[UUID, ...], chunk_ids: tuple[UUID, ...]
    ) -> tuple[Passage, ...]: ...


class Reranker(Protocol):
    def rank(
        self,
        queries: tuple[str, ...],
        passages: tuple[Passage, ...],
        fused: dict[UUID, float],
    ) -> tuple[tuple[Passage, float], ...]: ...


class EvidenceSearch(Protocol):
    """One scoped retrieval strategy; the agent and a baseline can share it."""

    def search(
        self,
        identity: Identity,
        source_ids: tuple[UUID, ...],
        queries: tuple[str, ...],
        round_number: int,
    ) -> RetrievalResult: ...


class AnswerWorkflow(Protocol):
    def run(
        self,
        identity: Identity,
        question: str,
        history: tuple[Message, ...],
        catalog: tuple[CatalogSource, ...],
    ) -> AgentOutcome: ...
