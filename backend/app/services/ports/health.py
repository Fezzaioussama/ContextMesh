"""Small port for a dependency's process-readiness diagnostic."""

from typing import Protocol


class DependencyHealth(Protocol):
    def ready(self) -> bool: ...
