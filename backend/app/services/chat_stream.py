"""Bounded delivery for a claimed chat turn, independent of the HTTP transport.

A disconnected reader detaches from delivery. Its already claimed, bounded agent
run finishes and persists normally, so reconnecting with the same key can replay it.
The fixed worker pool and admission slots prevent abandoned readers growing workers.
"""

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from queue import Empty, Full, Queue
from threading import BoundedSemaphore, Event

from sqlalchemy.exc import SQLAlchemyError

from app.services.models import TurnResult
from app.utils.errors import ContextMeshError
from app.utils.logging import flow_event
from app.utils.streaming import StreamEvent, event_scope

WORKERS = ThreadPoolExecutor(max_workers=8, thread_name_prefix="chat-stream")
SLOTS = BoundedSemaphore(8)
HEARTBEAT_SECONDS = 2.0


class ChatStream:
    """One claimed turn delivered through a bounded event queue.

    The turn runs on the shared worker pool. The admission slot that
    `ConversationService.open_stream` acquired is released when the turn finishes,
    whether or not anyone is still reading.
    """

    def __init__(self, answer: Callable[[], TurnResult]):
        self._events: Queue[StreamEvent | TurnResult] = Queue(maxsize=128)
        self._closed = Event()
        self._had_delta = False
        self._answer = answer
        self._events.put(StreamEvent("progress", {"stage": "started", "message": "Thinking…"}))
        self.finished = WORKERS.submit(copy_context().run, self._run)
        self.finished.add_done_callback(lambda future: SLOTS.release())

    def events(self) -> Iterator[StreamEvent | TurnResult]:
        """Yield queued events until the turn result or an error, then close delivery.

        A `working` progress event every `HEARTBEAT_SECONDS` of silence keeps proxies
        and readers from timing out during a long search.
        """
        try:
            while not self._closed.is_set():
                try:
                    event = self._events.get(timeout=HEARTBEAT_SECONDS)
                except Empty:
                    event = StreamEvent("progress", {"stage": "working", "message": "Working…"})
                yield event
                if isinstance(event, TurnResult) or event.event == "error":
                    return
        finally:
            self.close()

    def close(self) -> None:
        """Drop delivery buffers; do not cancel a turn halfway through canonical saving."""
        self._closed.set()
        self._drain()

    def _emit(self, event: StreamEvent) -> None:
        """Queue an event from the turn's thread; after `close`, events are dropped.

        Raises:
            ContextMeshError: `provider_unavailable` when the reader stopped keeping up
                and the queue stays full, so output never accumulates without bound.
        """
        if event.event == "delta":
            self._had_delta = True
        if self._closed.is_set():
            return
        try:
            self._events.put(event, timeout=0.25)
        except Full:
            raise ContextMeshError("provider_unavailable") from None

    def _run(self) -> None:
        """Run the turn with this stream as the event sink, ending with one terminal item.

        A turn that streamed no delta, such as a grounded answer, sends its saved text as
        a single delta before the result. Any failure becomes a safe error event.
        """
        with event_scope(self._emit):
            try:
                result = self._answer()
                if not self._had_delta:
                    self._emit(StreamEvent("delta", {"text": result.assistant_message.content}))
                terminal = result
            except Exception as error:
                terminal = _failure(error)
            self._finish(terminal)

    def _finish(self, terminal: StreamEvent | TurnResult) -> None:
        """Queue the result or error, dropping buffered events if needed so it always fits."""
        if self._closed.is_set():
            return
        try:
            self._events.put(terminal, timeout=0.25)
        except Full:
            self._drain()
            self._events.put_nowait(terminal)
        flow_event("ask_question", "stream_ended", completed=isinstance(terminal, TurnResult))

    def _drain(self) -> None:
        """Discard every buffered event."""
        while True:
            try:
                self._events.get_nowait()
            except Empty:
                return


def _failure(error: Exception) -> StreamEvent:
    """The safe public error event for a failed turn.

    A database error is `database_unavailable`; an unknown error is `internal_error` with
    a generic message and no details.
    """
    if isinstance(error, SQLAlchemyError):
        error = ContextMeshError("database_unavailable")
    if isinstance(error, ContextMeshError):
        return StreamEvent(
            "error", {"code": error.code, "message": error.message, "retryable": error.retryable}
        )
    return StreamEvent(
        "error",
        {
            "code": "internal_error",
            "message": "The request could not be completed.",
            "retryable": False,
        },
    )
