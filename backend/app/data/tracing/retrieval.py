"""Evidence search wrapped in a LangSmith retriever span listing the hydrated passages."""

from collections.abc import Mapping
from uuid import UUID

from langsmith import traceable

from app.services.ports.retrieval import EvidenceSearch, RetrievalResult
from app.services.rules.knowledge import Evidence
from app.utils.security import Identity


class TracedEvidenceSearch:
    def __init__(self, inner: EvidenceSearch):
        self._search = traceable(
            run_type="retriever",
            name="hybrid_search",
            process_inputs=_search_inputs,
            process_outputs=_search_outputs,
        )(inner.search)

    def search(
        self,
        identity: Identity,
        source_ids: tuple[UUID, ...],
        queries: tuple[str, ...],
        round_number: int,
    ) -> RetrievalResult:
        return self._search(identity, source_ids, queries, round_number)


def _search_inputs(inputs: Mapping[str, object]) -> dict:
    return {
        "queries": list(inputs["queries"]),
        "source_ids": [str(source_id) for source_id in inputs["source_ids"]],
        "round": inputs["round_number"],
    }


def _search_outputs(result: RetrievalResult) -> dict:
    """`documents` in LangChain's shape, so LangSmith renders them as retrieved docs."""
    return {
        "documents": [_document(item) for item in result.evidence],
        "candidates": result.candidates,
        "vector_available": result.vector_available,
    }


def _document(item: Evidence) -> dict:
    passage = item.passage
    return {
        "type": "Document",
        "page_content": passage.text,
        "metadata": {
            "chunk_id": str(passage.chunk_id),
            "source": passage.source_name,
            "title": passage.title,
            "heading_path": list(passage.locator.heading_path),
            "lines": [passage.locator.line_start, passage.locator.line_end],
            "score": item.score,
            "round": item.round,
        },
    }
