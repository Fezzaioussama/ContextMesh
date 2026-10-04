"""Safe failures shared across modules; transport adapters choose status codes."""


class ContextMeshError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def unavailable_resource() -> ContextMeshError:
    return ContextMeshError("not_found", "The requested resource is unavailable.")


def invalid_input(message: str = "The request contains invalid input.") -> ContextMeshError:
    return ContextMeshError("invalid_input", message)
