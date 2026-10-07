"""Conversation and knowledge failures independent of HTTP and provider SDK exceptions."""

from app.core.exceptions import ContextMeshError


class IngestionFailure(Exception):
    """A job failure whose safe code is stored; retryable failures are rescheduled."""

    def __init__(self, code: str, retryable: bool = False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class FetchFailure(Exception):
    """A web fetch outcome with a safe code; never carries response content."""

    def __init__(self, code: str, retryable: bool = False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class LeaseLost(Exception):
    """The worker no longer owns the job's fencing token and must stop writing."""


class VectorIndexUnavailable(Exception):
    """The vector projection cannot be queried; canonical lexical retrieval still works."""


def unsupported_media_type() -> ContextMeshError:
    return ContextMeshError(
        "unsupported_media_type",
        "This file type is not supported. Use PDF, Word, PowerPoint, Excel, HTML, "
        "Markdown, or a text-based format.",
    )


def wrong_source_kind() -> ContextMeshError:
    return ContextMeshError(
        "wrong_source_kind", "This operation is not available for this kind of source."
    )


def payload_too_large(limit: int) -> ContextMeshError:
    return ContextMeshError("payload_too_large", f"Uploads are limited to {limit} bytes.")


def forbidden_operation() -> ContextMeshError:
    return ContextMeshError("forbidden", "Your role does not permit changing this source.")


def evidence_changed() -> ContextMeshError:
    return ContextMeshError(
        "evidence_changed",
        "Sources changed while the answer was prepared. Retry the message.",
        retryable=True,
    )


def agent_deadline_exceeded() -> ContextMeshError:
    return ContextMeshError(
        "agent_deadline_exceeded",
        "The agent ran out of time. Retry or narrow the question.",
        retryable=True,
    )


def agent_budget_exceeded() -> ContextMeshError:
    return ContextMeshError(
        "agent_budget_exceeded", "The agent reached its token budget. Narrow the question."
    )


def embedding_input_rejected() -> ContextMeshError:
    return ContextMeshError(
        "embedding_input_rejected", "The embedding provider rejected this text as input."
    )


def model_output_limit() -> ContextMeshError:
    return ContextMeshError(
        "model_output_limit",
        "The model used its whole output budget. Raise CONTEXTMESH_MAX_OUTPUT_TOKENS.",
        retryable=True,
    )


def model_output_invalid() -> ContextMeshError:
    return ContextMeshError(
        "model_output_invalid", "The model returned an unusable response. Retry later.", True
    )


def turn_in_progress() -> ContextMeshError:
    return ContextMeshError(
        "turn_in_progress", "A turn is already running. Retry shortly.", retryable=True
    )


def idempotency_conflict() -> ContextMeshError:
    return ContextMeshError(
        "idempotency_conflict", "This idempotency key was used with different input."
    )


def provider_not_configured() -> ContextMeshError:
    return ContextMeshError(
        "provider_not_configured", "Configure the model provider to send messages."
    )


def provider_unavailable() -> ContextMeshError:
    return ContextMeshError(
        "provider_unavailable", "The model provider is unavailable. Retry later.", retryable=True
    )
