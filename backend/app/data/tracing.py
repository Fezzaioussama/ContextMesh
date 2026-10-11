"""LangSmith tracing: one root run per turn, with each model call and search nested
under the graph node that made it.

Tracing is opt-in because traces carry full prompts, passages, and answers. The wrappers
keep the inner client's behavior, errors, and budgets exactly; outside a tracing scope
they upload nothing.
"""

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager

from langsmith import Client, traceable, tracing_context

from app.services.models import (
    ChoiceReply,
    ChoiceTask,
    DecisionModel,
    EmbeddingModel,
    Evidence,
    ReasoningModel,
    RetrievalResult,
    StructuredReply,
    StructuredTask,
    Usage,
)


class LangSmithTracing:
    """Sends the runs started inside `scope` to one project."""

    def __init__(self, *, api_key: str, project: str, endpoint: str, client: Client | None = None):
        self._client = client or Client(api_key=api_key, api_url=endpoint)
        self._project = project

    def scope(self, name: str, ids: Mapping[str, str]) -> AbstractContextManager[object]:
        return tracing_context(
            enabled=True,
            client=self._client,
            project_name=self._project,
            tags=["contextmesh", name],
            metadata=dict(ids),
        )

    def close(self) -> None:
        """Send runs still buffered by the background uploader before shutdown."""
        self._client.flush()


class DisabledTracing:
    """Keeps a run untraced even when legacy `LANGCHAIN_TRACING_V2`-style variables
    would otherwise make LangGraph upload it with a client built from the environment."""

    def scope(self, name: str, ids: Mapping[str, str]) -> AbstractContextManager[object]:
        return tracing_context(enabled=False)


class TracedReasoningModel:
    def __init__(self, inner: ReasoningModel, *, provider: str, model: str):
        self._inner = inner
        self._complete = traceable(
            run_type="llm",
            metadata={"ls_provider": provider, "ls_model_name": model},
            process_inputs=lambda inputs: {
                "messages": [
                    {"role": "system", "content": inputs["task"].instructions},
                    {"role": "user", "content": inputs["task"].content},
                ],
                "response_schema": dict(inputs["task"].schema),
            },
            process_outputs=lambda reply: {
                "output": dict(reply.data),
                "usage_metadata": _usage(reply.usage),
            },
        )(inner.complete)

    @property
    def configured(self) -> bool:
        return self._inner.configured

    def complete(self, task: StructuredTask) -> StructuredReply:
        return self._complete(task, langsmith_extra={"name": task.name})


class TracedDecisionModel:
    def __init__(self, inner: DecisionModel, *, model: str):
        self._inner = inner
        self._choose = traceable(
            run_type="llm",
            metadata={"ls_provider": "openrouter", "ls_model_name": model},
            process_inputs=lambda inputs: {
                "instructions": inputs["task"].instructions,
                "state": dict(inputs["task"].state),
                "criteria": dict(inputs["task"].criteria),
            },
            process_outputs=lambda reply: {
                "choice": reply.choice,
                "probabilities": dict(reply.probabilities),
                "confidence": reply.confidence,
                "usage_metadata": _usage(reply.usage),
            },
        )(inner.choose)

    @property
    def configured(self) -> bool:
        return self._inner.configured

    def choose(self, task: ChoiceTask) -> ChoiceReply:
        return self._choose(task, langsmith_extra={"name": task.name})


class TracedEmbeddingModel:
    def __init__(self, inner: EmbeddingModel):
        self._inner = inner
        self._embed = traceable(
            run_type="embedding",
            name="embed",
            metadata={"embedding_model": inner.identity},
            # Vectors are large and unreadable; their count and size are what matter.
            process_outputs=lambda vectors: {
                "vectors": len(vectors),
                "dimensions": len(vectors[0]) if vectors else 0,
            },
        )(inner.embed)

    @property
    def identity(self) -> str:
        return self._inner.identity

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return self._embed(texts)


class TracedSearch:
    """A retriever span listing the hydrated passages in LangChain's document shape, so
    LangSmith renders them as retrieved documents."""

    def __init__(self, inner):
        self._search = traceable(
            run_type="retriever",
            name="hybrid_search",
            process_inputs=lambda inputs: {
                "questions": list(inputs["queries"].questions),
                "keywords": list(inputs["queries"].keywords),
                "hypothetical": inputs["queries"].hypothetical,
                "source_ids": [str(source_id) for source_id in inputs["source_ids"]],
                "round": inputs["round_number"],
            },
            process_outputs=lambda result: {
                "documents": [_document(item) for item in result.evidence],
                "candidates": result.candidates,
                "vector_available": result.vector_available,
            },
        )(inner.search)

    def search(self, identity, source_ids, queries, round_number) -> RetrievalResult:
        return self._search(identity, source_ids, queries, round_number)


def _usage(usage: Usage) -> dict[str, int]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total,
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
