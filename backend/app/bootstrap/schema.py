"""Assemble module tables for Alembic without changing canonical table identity."""

from app.db.base import metadata
from app.db.models.conversation import (
    conversations,
    messages,
    turns,
)
from app.db.models.user import memberships, workspaces

SCHEMA_REVISION = "0001_assistant"
REQUIRED_TABLES = frozenset({"alembic_version", *metadata.tables})

__all__ = ["metadata", "workspaces", "memberships", "conversations", "messages", "turns"]
