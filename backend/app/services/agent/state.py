"""Typed workflow state; graph adapters carry it but never interpret its policy."""

from dataclasses import dataclass, field, replace
from uuid import UUID

from app.core.security import Identity
from app.domain.answers import TraceStage
from app.domain.knowledge import CatalogSource, Evidence
from app.domain.models import Message, Usage


@dataclass(frozen=True)
class DraftClaim:
    text: str
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class Draft:
    status: str
    claims: tuple[DraftClaim, ...]
    gaps: tuple[str, ...]
    context: tuple[Evidence, ...]


@dataclass(frozen=True)
class AgentState:
    identity: Identity
    request: str
    history: tuple[Message, ...]
    catalog: tuple[CatalogSource, ...]
    deadline: float
    question: str = ""
    selected: tuple[UUID, ...] = ()
    queries: tuple[str, ...] = ()
    searched: frozenset[UUID] = frozenset()
    asked: frozenset[str] = frozenset()
    rounds: int = 0
    evidence: tuple[Evidence, ...] = ()
    consulted: frozenset[UUID] = frozenset()
    gaps: tuple[str, ...] = ()
    ready: bool = False
    draft: Draft | None = None
    unsupported: tuple[int, ...] = ()
    repairs: int = 0
    trace: tuple[TraceStage, ...] = ()
    usage: Usage = field(default_factory=lambda: Usage(0, 0))

    @property
    def searchable(self) -> tuple[CatalogSource, ...]:
        return tuple(source for source in self.catalog if source.searchable_count > 0)

    @property
    def unsearched(self) -> tuple[UUID, ...]:
        return tuple(source.id for source in self.searchable if source.id not in self.searched)

    def source_name(self, source_id: UUID) -> str:
        names = {source.id: source.name for source in self.catalog}
        return names.get(source_id, "Unknown source")

    def traced(self, stage: str, summary: str, **changes: object) -> "AgentState":
        return replace(self, trace=(*self.trace, TraceStage(stage, summary)), **changes)

    def charged(self, usage: Usage) -> "AgentState":
        return replace(self, usage=self.usage + usage)


def normalized_query(query: str) -> str:
    return " ".join(query.casefold().split())
