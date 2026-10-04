"""Conversation failures independent of HTTP and provider SDK exceptions."""

from app.core.exceptions import ContextMeshError


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
