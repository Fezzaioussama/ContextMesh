"""Saved grounded answers and citations that reference canonical chunks."""

from sqlalchemy import (
    Column,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import metadata

answers = Table(
    "answers",
    metadata,
    Column("message_id", Uuid, ForeignKey("messages.id"), primary_key=True),
    Column("status", String(30), nullable=False),
    Column("claims", JSONB, nullable=False),
    Column("gaps", JSONB, nullable=False),
    Column("trace", JSONB, nullable=False),
    Column("consulted_document_ids", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
)

citations = Table(
    "citations",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("message_id", Uuid, ForeignKey("messages.id"), nullable=False),
    Column("number", Integer, nullable=False),
    Column("chunk_id", Uuid, ForeignKey("chunks.id"), nullable=False),
    Column("source_id", Uuid, ForeignKey("sources.id"), nullable=False),
    Column("document_id", Uuid, ForeignKey("documents.id"), nullable=False),
    Column("document_version_id", Uuid, ForeignKey("document_versions.id"), nullable=False),
    Column("index_generation_id", Uuid, ForeignKey("index_generations.id"), nullable=False),
    Column("title", String(200), nullable=False),
    Column("locator", JSONB, nullable=False),
    Column("snippet", Text, nullable=False),
    Column("source_url", Text),
    UniqueConstraint("message_id", "number", name="uq_citation_number"),
)
