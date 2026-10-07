"""Database engine construction; bootstrap owns disposal and transaction adapters."""

from sqlalchemy import Engine, create_engine


def create_database_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True, connect_args={"connect_timeout": 3})
