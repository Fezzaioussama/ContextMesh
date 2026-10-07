"""Database readiness adapter; dependency failures become an unavailable result."""

from sqlalchemy import Engine, inspect
from sqlalchemy.exc import SQLAlchemyError


class DatabaseHealth:
    def __init__(self, engine: Engine, required_tables: frozenset[str], revision: str):
        self._engine = engine
        self._required_tables = required_tables
        self._revision = revision

    def ready(self) -> bool:
        try:
            return self._schema_ready()
        except SQLAlchemyError:
            return False

    def _schema_ready(self) -> bool:
        with self._engine.connect() as connection:
            available = set(inspect(connection).get_table_names())
            if not self._required_tables.issubset(available):
                return False
            version = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
            return version == self._revision
