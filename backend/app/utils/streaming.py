"""Transport-neutral, request-local delivery of public answer events.

This module carries no application policy or HTTP serialization. A missing sink
makes ordinary model and service calls behave exactly as non-streaming calls.
"""

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class StreamEvent:
    """One public event for the reader: its SSE event name and JSON data."""

    event: Literal["progress", "delta", "completed", "error"]
    data: Mapping[str, object]


_sink: ContextVar[Callable[[StreamEvent], None] | None] = ContextVar("answer_sink", default=None)


@contextmanager
def event_scope(sink: Callable[[StreamEvent], None]) -> Iterator[None]:
    """Send the events emitted inside this block to `sink`, then restore the previous sink.

    The sink lives in a context variable, so it follows the current context only: a
    concurrent request without a scope keeps behaving as a non-streaming call.
    """
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


def emit(event: StreamEvent) -> None:
    """Deliver `event` to the current sink; outside a scope it is dropped."""
    sink = _sink.get()
    if sink is not None:
        sink(event)


def streaming() -> bool:
    """Whether a reader is listening, so a model client can pick its streaming transport."""
    return _sink.get() is not None
