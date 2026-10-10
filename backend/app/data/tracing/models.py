"""Model ports wrapped in LangSmith spans; each call nests under its graph node.

Setup applies them only when tracing is on, and only to the agent's ports, which
run inside the turn's tracing scope. The wrapped behavior, errors, and budgets are
exactly those of the inner adapter.
"""

from collections.abc import Mapping, Sequence

from langsmith import traceable

from app.services.ports.decisions import ChoiceReply, ChoiceTask, DecisionModel
from app.services.ports.models import (
    EmbeddingModel,
    ReasoningModel,
    StructuredReply,
    StructuredTask,
)
from app.services.rules.models import Usage


class TracedReasoningModel:
    def __init__(self, inner: ReasoningModel, *, provider: str, model: str):
        self._inner = inner
        self._complete = traceable(
            run_type="llm",
            metadata=_model_metadata(provider, model),
            process_inputs=_task_inputs,
            process_outputs=_reply_outputs,
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
            metadata=_model_metadata("openrouter", model),
            process_inputs=_choice_inputs,
            process_outputs=_choice_outputs,
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
            process_outputs=_vector_outputs,
        )(inner.embed)

    @property
    def identity(self) -> str:
        return self._inner.identity

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return self._embed(texts)


def _model_metadata(provider: str, model: str) -> dict[str, str]:
    """LangSmith's names for the provider and model, used for cost and filtering."""
    return {"ls_provider": provider, "ls_model_name": model}


def _usage_metadata(usage: Usage) -> dict[str, int]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total,
    }


def _task_inputs(inputs: Mapping[str, object]) -> dict:
    task = inputs["task"]
    return {
        "messages": [
            {"role": "system", "content": task.instructions},
            {"role": "user", "content": task.content},
        ],
        "response_schema": dict(task.schema),
    }


def _reply_outputs(reply: StructuredReply) -> dict:
    return {"output": dict(reply.data), "usage_metadata": _usage_metadata(reply.usage)}


def _choice_inputs(inputs: Mapping[str, object]) -> dict:
    task = inputs["task"]
    return {
        "instructions": task.instructions,
        "state": dict(task.state),
        "criteria": dict(task.criteria),
    }


def _choice_outputs(reply: ChoiceReply) -> dict:
    return {
        "choice": reply.choice,
        "probabilities": dict(reply.probabilities),
        "confidence": reply.confidence,
        "usage_metadata": _usage_metadata(reply.usage),
    }


def _vector_outputs(vectors: tuple[tuple[float, ...], ...]) -> dict:
    """Vectors are large and unreadable; their count and size are what matter."""
    return {"vectors": len(vectors), "dimensions": len(vectors[0]) if vectors else 0}
