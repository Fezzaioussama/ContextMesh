"""Assemble module tables for Alembic without changing canonical table identity."""

from app.db.base import metadata
from app.db.models.answers import answers, citations
from app.db.models.conversation import (
    conversations,
    messages,
    turns,
)
from app.db.models.knowledge import (
    chunks,
    document_versions,
    documents,
    index_generations,
    jobs,
    sources,
)
from app.db.models.user import memberships, workspaces

SCHEMA_REVISION = "0003_consulted_documents"
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
