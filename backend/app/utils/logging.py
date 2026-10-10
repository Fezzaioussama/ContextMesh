"""Shared loggers; handlers emit safe identifiers rather than request content.

`flow_event` writes one line per user-story step, so a workflow can be followed in the
API and worker logs, e.g. `grep "flow=crawl_website"`. docs/flows.md lists every step.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

http_logger = logging.getLogger("context_mesh.http")
flow_logger = logging.getLogger("context_mesh.flow")
_scope: ContextVar[dict[str, object]] = ContextVar("flow_scope", default={})

# Which user story each durable job belongs to.
JOB_FLOWS = {
    "document.index_requested": "upload_document",
    "source.sync_requested": "crawl_website",
    "document.delete_requested": "delete_document",
    "source.delete_requested": "delete_source",
}


def flow_event(flow: str, step: str, **ids: object) -> None:
    """Log `flow=<story> step=<step> key=value…`: identifiers and counts only, never
    document text, questions, or answers. Ids from enclosing `flow_scope`s come first."""
    details = "".join(f" {name}={value}" for name, value in {**_scope.get(), **ids}.items())
    flow_logger.info("flow=%s step=%s%s", flow, step, details)


@contextmanager
def flow_scope(**ids: object) -> Iterator[None]:
    """Add ids (e.g. `turn_id`, `job_id`) to every flow line logged inside the block,
    so one run can be followed with `grep "turn_id=<id>"` even when runs overlap."""
    token = _scope.set({**_scope.get(), **ids})
    try:
        yield
    finally:
        _scope.reset(token)


def job_flow(kind: str) -> str:
    return JOB_FLOWS.get(kind, kind)


def show_flows() -> None:
    """Print flow lines at INFO in both processes (uvicorn configures only its own loggers)."""
    if flow_logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
    flow_logger.addHandler(handler)
    flow_logger.setLevel(logging.INFO)
    flow_logger.propagate = False
