"""LangSmith run scope: LangGraph nodes and traced ports nest under one root run."""

from collections.abc import Mapping
from contextlib import AbstractContextManager

from langsmith import Client, tracing_context


class LangSmithTracing:
    """Sends the runs started inside `scope` to one project."""

    def __init__(self, *, api_key: str, project: str, endpoint: str, client: Client | None = None):
        self._client = client or Client(api_key=api_key, api_url=endpoint)
        self._project = project

    def scope(self, name: str, ids: Mapping[str, str]) -> AbstractContextManager[object]:
        return tracing_context(
            enabled=True,
            client=self._client,
            project_name=self._project,
            tags=["contextmesh", name],
            metadata=dict(ids),
        )

    def close(self) -> None:
        """Send runs still buffered by the background uploader before shutdown."""
        self._client.flush()


class DisabledTracing:
    """Keeps a run untraced even when legacy `LANGCHAIN_TRACING_V2`-style variables
    would otherwise make LangGraph upload it with a client built from the environment."""

    def scope(self, name: str, ids: Mapping[str, str]) -> AbstractContextManager[object]:
        return tracing_context(enabled=False)
