"""Apply versioned PostgreSQL schema with the same server configuration as the API."""

from alembic import context
from app.bootstrap.schema import metadata
from app.core.config import Settings
from sqlalchemy import create_engine


def offline():
    context.configure(
        url=Settings().database_url,
        target_metadata=metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def online():
    engine = create_engine(Settings().database_url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


def run():
    if context.is_offline_mode():
        offline()
    else:
        online()


run()
