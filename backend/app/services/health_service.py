"""Readiness policy independent of SQLAlchemy and HTTP."""

from app.core.exceptions import ContextMeshError
from app.services.ports.health import DependencyHealth


class HealthService:
    def __init__(self, database: DependencyHealth):
        self._database = database

    def require_ready(self) -> None:
        if not self._database.ready():
            raise ContextMeshError(
                "database_unavailable", "The database schema is unavailable.", retryable=True
            )
