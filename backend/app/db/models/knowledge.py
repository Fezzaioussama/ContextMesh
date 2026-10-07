"""Canonical source, version, generation, chunk, and durable job tables."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR

from app.db.base import metadata

SEARCH_VECTOR = "to_tsvector('english', coalesce(heading, '') || ' ' || content)"

sources = Table(
    "sources",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("workspace_id", Uuid, ForeignKey("workspaces.id"), nullable=False),
    Column("kind", String(20), nullable=False),
    Column("name", String(100), nullable=False),
    Column("description", String(500), nullable=False),
    Column("owner_subject", String(200), nullable=False),
    Column("url", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("deleted_at", DateTime(timezone=True)),
    CheckConstraint("kind IN ('upload', 'website')", name="ck_source_kind"),
)
Index("ix_sources_scope", sources.c.workspace_id, sources.c.created_at, sources.c.id)

documents = Table(
    "documents",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("workspace_id", Uuid, ForeignKey("workspaces.id"), nullable=False),
    Column("source_id", Uuid, ForeignKey("sources.id"), nullable=False),
    Column("external_id", Text, nullable=False),
    Column("title", String(200), nullable=False),
    Column("media_type", String(100), nullable=False),
    Column("source_uri", Text),
    Column("latest_version_id", Uuid),
    Column("active_generation_id", Uuid),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("deleted_at", DateTime(timezone=True)),
)
Index(
    "uq_live_document",
    documents.c.source_id,
    documents.c.external_id,
    unique=True,
    postgresql_where=text("deleted_at IS NULL"),
)

document_versions = Table(
    "document_versions",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("document_id", Uuid, ForeignKey("documents.id"), nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("blob_key", String(100), nullable=False),
    Column("byte_size", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

index_generations = Table(
    "index_generations",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("document_id", Uuid, ForeignKey("documents.id"), nullable=False),
    Column("document_version_id", Uuid, ForeignKey("document_versions.id"), nullable=False),
    Column("pipeline_signature", String(300), nullable=False),
    Column("state", String(20), nullable=False),
    Column("expected_chunks", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True)),
    UniqueConstraint("document_version_id", "pipeline_signature", name="uq_generation_target"),
    CheckConstraint("state IN ('pending', 'published', 'superseded')", name="ck_generation_state"),
)

chunks = Table(
    "chunks",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("generation_id", Uuid, ForeignKey("index_generations.id"), nullable=False),
    Column("document_id", Uuid, ForeignKey("documents.id"), nullable=False),
    Column("source_id", Uuid, ForeignKey("sources.id"), nullable=False),
    Column("workspace_id", Uuid, ForeignKey("workspaces.id"), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("heading", Text, nullable=False),
    Column("heading_path", JSONB, nullable=False),
    Column("content", Text, nullable=False),
    Column("line_start", Integer, nullable=False),
    Column("line_end", Integer, nullable=False),
    Column("page", Integer),
    Column("slide", Integer),
    Column("token_count", Integer, nullable=False),
    Column("search_vector", TSVECTOR, Computed(SEARCH_VECTOR, persisted=True)),
    UniqueConstraint("generation_id", "ordinal", name="uq_chunk_ordinal"),
)
Index("ix_chunks_search", chunks.c.search_vector, postgresql_using="gin")
Index("ix_chunks_generation", chunks.c.generation_id)

jobs = Table(
    "jobs",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("workspace_id", Uuid, ForeignKey("workspaces.id"), nullable=False),
    Column("kind", String(40), nullable=False),
    Column("source_id", Uuid, ForeignKey("sources.id"), nullable=False),
    Column("document_id", Uuid, ForeignKey("documents.id")),
    Column("document_version_id", Uuid, ForeignKey("document_versions.id")),
    Column("status", String(20), nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("max_attempts", Integer, nullable=False),
    Column("fencing_token", BigInteger, nullable=False),
    Column("lease_owner", String(100)),
    Column("lease_until", DateTime(timezone=True)),
    Column("next_attempt_at", DateTime(timezone=True), nullable=False),
    Column("stage", String(30)),
    Column("safe_error_code", String(50)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "kind IN ('document.index_requested', 'document.delete_requested', "
        "'source.delete_requested', 'source.sync_requested')",
        name="ck_job_kind",
    ),
    CheckConstraint(
        "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
        name="ck_job_status",
    ),
)
Index("ix_jobs_due", jobs.c.status, jobs.c.next_attempt_at)
Index("ix_jobs_document", jobs.c.document_id, jobs.c.created_at)
