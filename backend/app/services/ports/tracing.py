"""Application-owned tracing port: one scope groups a workflow run's nested spans."""

from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Protocol


class RunTracing(Protocol):
    def scope(self, name: str, ids: Mapping[str, str]) -> AbstractContextManager[object]:
        """Trace everything run inside the block as one named run tagged with `ids`."""
        ...
