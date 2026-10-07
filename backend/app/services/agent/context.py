"""Evidence pooling, bounded context selection, and model payload construction."""

from uuid import UUID

from app.domain.knowledge import CatalogSource, Evidence
from app.domain.models import Message
from app.services.agent.policy import AgentPolicy


def merged_evidence(
    existing: tuple[Evidence, ...], incoming: tuple[Evidence, ...], limit: int
) -> tuple[Evidence, ...]:
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
        if _admit(taken, item, policy.max_passages_per_document):
            chosen.append(item)
    return tuple(chosen)


def _admit(taken: dict[UUID, int], item: Evidence, per_document: int) -> bool:
    count = taken.get(item.passage.document_id, 0)
    taken[item.passage.document_id] = count + 1
    return count < per_document


def _strength(item: Evidence) -> tuple[float, str]:
    return (-item.score, str(item.passage.chunk_id))


def labeled(context: tuple[Evidence, ...]) -> dict[str, Evidence]:
    return {f"E{index}": item for index, item in enumerate(context, start=1)}


def passages_payload(context: tuple[Evidence, ...]) -> list[dict[str, str]]:
    return [_passage(label, item) for label, item in labeled(context).items()]


def _passage(label: str, item: Evidence) -> dict[str, str]:
    passage = item.passage
    return {
        "id": label,
        "source": passage.source_name,
        "title": passage.title,
        "section": " > ".join(passage.locator.heading_path),
        "text": passage.text,
    }


def conversation_payload(history: tuple[Message, ...], limit: int) -> list[dict[str, str]]:
    return [
        {"role": message.role, "content": message.content[:1500]} for message in history[-limit:]
    ]


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
