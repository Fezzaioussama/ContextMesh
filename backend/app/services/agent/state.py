"""Agent state, server-owned limits, and helpers that build model input and check model
output. Model output can never raise a limit, and malformed output is a safe failure."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Literal
from uuid import UUID

from app.services.models import CatalogSource, Evidence, Identity, Message, TraceStage, Usage
from app.utils.errors import ContextMeshError

Route = Literal["retrieve", "direct"]


@dataclass(frozen=True)
class AgentPolicy:
    max_rounds: int = 3
    max_query_variants: int = 2
    max_repairs: int = 1
    deadline_seconds: float = 60.0
    call_timeout_seconds: float = 45.0
    answer_reserve_seconds: float = 20.0
    round_reserve_seconds: float = 10.0
    minimum_call_seconds: float = 2.0
    direct_route_confidence: float = 0.8
    max_total_tokens: int = 60_000
    max_evidence: int = 24
    max_context_passages: int = 8
    max_passages_per_document: int = 3
    max_claims: int = 10
    max_gaps: int = 5
    history_messages: int = 6
    route_history_requests: int = 3


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
    """Everything one run knows. Each step returns a new state with its trace stage."""

    identity: Identity
    request: str
    history: tuple[Message, ...]
    catalog: tuple[CatalogSource, ...]
    deadline: float
    route: Route = "retrieve"
    direct_response: str = ""
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


def counted(count: int, singular: str, plural: str = "") -> str:
    """Readable counts for trace summaries: `1 source`, `2 sources`."""
    word = singular if count == 1 else plural or f"{singular}s"
    return f"{count} {word}"


# Evidence pooling and bounded context


def _strength(item: Evidence) -> tuple[float, str]:
    return (-item.score, str(item.passage.chunk_id))


def merged_evidence(
    existing: tuple[Evidence, ...], incoming: tuple[Evidence, ...], limit: int
) -> tuple[Evidence, ...]:
    """The best-scored copy of each chunk, strongest first."""
    best: dict[UUID, Evidence] = {}
    for item in (*existing, *incoming):
        current = best.get(item.passage.chunk_id)
        if current is None or item.score > current.score:
            best[item.passage.chunk_id] = item
    return tuple(sorted(best.values(), key=_strength))[:limit]


def selected_context(evidence: tuple[Evidence, ...], policy: AgentPolicy) -> tuple[Evidence, ...]:
    """Prefer strong passages while capping how many come from one document."""
    taken: dict[UUID, int] = {}
    chosen: list[Evidence] = []
    for item in sorted(evidence, key=_strength):
        if len(chosen) == policy.max_context_passages:
            break
        document_id = item.passage.document_id
        taken[document_id] = taken.get(document_id, 0) + 1
        if taken[document_id] <= policy.max_passages_per_document:
            chosen.append(item)
    return tuple(chosen)


# Model input: passages are labelled E1, E2, ... so replies can cite them


def labeled(context: tuple[Evidence, ...]) -> dict[str, Evidence]:
    return {f"E{index}": item for index, item in enumerate(context, start=1)}


def passages_payload(context: tuple[Evidence, ...]) -> list[dict[str, str]]:
    return [
        {
            "id": label,
            "source": item.passage.source_name,
            "title": item.passage.title,
            "section": " > ".join(item.passage.locator.heading_path),
            "text": item.passage.text,
        }
        for label, item in labeled(context).items()
    ]


def conversation_payload(history: tuple[Message, ...], limit: int) -> list[dict[str, str]]:
    return [
        {"role": message.role, "content": message.content[:1500]} for message in history[-limit:]
    ]


def request_payload(history: tuple[Message, ...], limit: int) -> list[str]:
    """Only the user's own earlier requests: assistant replies bias a route classifier."""
    requests = [message.content[:1500] for message in history if message.role == "user"]
    return requests[max(len(requests) - limit, 0) :]


def catalog_payload(sources: tuple[CatalogSource, ...]) -> list[dict[str, object]]:
    return [
        {
            "id": str(source.id),
            "name": source.name,
            "description": source.description,
            "documents": source.searchable_count,
        }
        for source in sources
    ]


# Model output checks


def _invalid() -> ContextMeshError:
    return ContextMeshError("model_output_invalid")


def _compact(value: str, limit: int) -> str:
    return " ".join(value.split())[:limit]


def text_field(data: Mapping[str, object], name: str, limit: int) -> str:
    value = data.get(name)
    if not isinstance(value, str):
        raise _invalid()
    return _compact(value, limit)


def flag(data: Mapping[str, object], name: str) -> bool:
    value = data.get(name)
    if not isinstance(value, bool):
        raise _invalid()
    return value


def objects(
    data: Mapping[str, object], name: str, max_items: int
) -> tuple[Mapping[str, object], ...]:
    value = data.get(name)
    if not isinstance(value, list):
        raise _invalid()
    return tuple(item for item in value if isinstance(item, dict))[:max_items]


def string_list(
    data: Mapping[str, object], name: str, max_items: int, max_length: int
) -> tuple[str, ...]:
    """Distinct non-empty strings, compacted and truncated; other items are dropped."""
    value = data.get(name)
    if not isinstance(value, list):
        raise _invalid()
    cleaned = (_compact(item, max_length) for item in value if isinstance(item, str))
    return tuple(dict.fromkeys(item for item in cleaned if item))[:max_items]


def known_sources(values: tuple[str, ...], sources: tuple[CatalogSource, ...]) -> tuple[UUID, ...]:
    """Only ids from the catalog survive; the model cannot widen the search scope."""
    allowed = {str(source.id): source.id for source in sources}
    keys = (value.strip().lower() for value in values)
    return tuple(dict.fromkeys(allowed[key] for key in keys if key in allowed))
