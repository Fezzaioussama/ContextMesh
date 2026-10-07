"""Assemble module tables for Alembic without changing canonical table identity."""

from app.data.db.base import metadata
from app.data.db.models.answers import answers, citations
from app.data.db.models.conversation import (
    conversations,
    messages,
    turns,
)
from app.data.db.models.knowledge import (
    chunks,
    document_versions,
    documents,
    index_generations,
    jobs,
    sources,
)
from app.data.db.models.user import memberships, workspaces

SCHEMA_REVISION = "0004_formats_and_websites"
REQUIRED_TABLES = frozenset({"alembic_version", *metadata.tables})

__all__ = [
    "metadata",
    "workspaces",
    "memberships",
    "conversations",
    "messages",
    "turns",
    "sources",
    "documents",
    "document_versions",
    "index_generations",
    "chunks",
    "jobs",
    "answers",
    "citations",
]
