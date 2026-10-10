"""Safe failures: stable codes and messages that never carry input or SDK details."""

# code: (HTTP status, default message, whether retrying may help)
ERRORS: dict[str, tuple[int, str, bool]] = {
    "invalid_input": (422, "The request contains invalid input.", False),
    "not_found": (404, "The requested resource is unavailable.", False),
    "forbidden": (403, "Your role does not permit changing this source.", False),
    "wrong_source_kind": (
        409,
        "This operation is not available for this kind of source.",
        False,
    ),
    "payload_too_large": (413, "The upload is too large.", False),
    "unsupported_media_type": (
        415,
        "This file type is not supported. Use PDF, Word, PowerPoint, Excel, HTML, "
        "Markdown, or a text-based format.",
        False,
    ),
    "turn_in_progress": (409, "A turn is already running. Retry shortly.", True),
    "idempotency_conflict": (409, "This idempotency key was used with different input.", False),
    "evidence_changed": (
        409,
        "Sources changed while the answer was prepared. Retry the message.",
        True,
    ),
    "provider_not_configured": (503, "Configure the model provider to send messages.", False),
    "provider_unavailable": (503, "The model provider is unavailable. Retry later.", True),
    "model_output_invalid": (503, "The model returned an unusable response. Retry later.", True),
    "model_output_limit": (
        503,
        "The model used its whole output budget. Raise CONTEXTMESH_MAX_OUTPUT_TOKENS.",
        True,
    ),
    "embedding_input_rejected": (
        422,
        "The embedding provider rejected this text as input.",
        False,
    ),
    "agent_deadline_exceeded": (
        503,
        "The agent ran out of time. Retry or narrow the question.",
        True,
    ),
    "agent_budget_exceeded": (
        503,
        "The agent reached its token budget. Narrow the question.",
        False,
    ),
    "database_unavailable": (503, "The database is unavailable.", True),
}


class ContextMeshError(Exception):
    """A failure reported to the client; its code sets the HTTP status and default text."""

    def __init__(self, code: str, message: str | None = None):
        self.status, default, self.retryable = ERRORS[code]
        self.code = code
        self.message = message or default
        super().__init__(self.message)


class IngestionFailure(Exception):
    """A worker job failure whose safe code is stored; retryable ones are rescheduled."""

    def __init__(self, code: str, retryable: bool = False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class FetchFailure(IngestionFailure):
    """A web fetch outcome with a safe code; never carries response content."""


class LeaseLost(Exception):
    """The worker no longer owns the job's fencing token and must stop writing."""


class VectorIndexUnavailable(Exception):
    """The vector index cannot be queried; PostgreSQL keyword search still works."""
