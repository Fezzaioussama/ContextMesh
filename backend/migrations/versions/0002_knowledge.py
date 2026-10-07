"""Sources, immutable versions, published generations, chunks, jobs, and citations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_knowledge"
down_revision = "0001_assistant"
branch_labels = None
depends_on = None

SEARCH_VECTOR = "to_tsvector('english', coalesce(heading, '') || ' ' || content)"


def upgrade():
    create_sources()
    create_documents()
    create_generations()
    create_chunks()
    create_jobs()
    create_answers()


def create_sources():
    op.create_table(
        "sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("owner_subject", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("kind IN ('upload')", name="ck_source_kind"),
    )
    op.create_index("ix_sources_scope", "sources", ["workspace_id", "created_at", "id"])


def create_documents():
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("media_type", sa.String(50), nullable=False),
        sa.Column("latest_version_id", sa.Uuid()),
        sa.Column("active_generation_id", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "uq_live_document",
        "documents",
        ["source_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "document_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("blob_key", sa.String(100), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def create_generations():
    op.create_table(
        "index_generations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column(
            "document_version_id",
            sa.Uuid(),
            sa.ForeignKey("document_versions.id"),
            nullable=False,
        ),
        sa.Column("pipeline_signature", sa.String(300), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("expected_chunks", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "document_version_id", "pipeline_signature", name="uq_generation_target"
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'published', 'superseded')", name="ck_generation_state"
        ),
    )


def create_chunks():
    op.create_table(
        "chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "generation_id", sa.Uuid(), sa.ForeignKey("index_generations.id"), nullable=False
        ),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("heading", sa.Text(), nullable=False),
        sa.Column("heading_path", postgresql.JSONB(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("line_start", sa.Integer(), nullable=False),
        sa.Column("line_end", sa.Integer(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column(
            "search_vector", postgresql.TSVECTOR(), sa.Computed(SEARCH_VECTOR, persisted=True)
        ),
        sa.UniqueConstraint("generation_id", "ordinal", name="uq_chunk_ordinal"),
    )
    op.create_index("ix_chunks_search", "chunks", ["search_vector"], postgresql_using="gin")
    op.create_index("ix_chunks_generation", "chunks", ["generation_id"])


def create_jobs():
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id")),
        sa.Column("document_version_id", sa.Uuid(), sa.ForeignKey("document_versions.id")),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("lease_owner", sa.String(100)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stage", sa.String(30)),
        sa.Column("safe_error_code", sa.String(50)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('document.index_requested', 'document.delete_requested', "
            "'source.delete_requested')",
            name="ck_job_kind",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_job_status",
        ),
    )
    op.create_index("ix_jobs_due", "jobs", ["status", "next_attempt_at"])
    op.create_index("ix_jobs_document", "jobs", ["document_id", "created_at"])


def create_answers():
    op.create_table(
        "answers",
        sa.Column("message_id", sa.Uuid(), sa.ForeignKey("messages.id"), primary_key=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("claims", postgresql.JSONB(), nullable=False),
        sa.Column("gaps", postgresql.JSONB(), nullable=False),
        sa.Column("trace", postgresql.JSONB(), nullable=False),
    )
    op.create_table(
        "citations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("message_id", sa.Uuid(), sa.ForeignKey("messages.id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), sa.ForeignKey("chunks.id"), nullable=False),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column(
            "document_version_id",
            sa.Uuid(),
            sa.ForeignKey("document_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "index_generation_id",
            sa.Uuid(),
            sa.ForeignKey("index_generations.id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("locator", postgresql.JSONB(), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.UniqueConstraint("message_id", "number", name="uq_citation_number"),
    )


def downgrade():
    op.drop_table("citations")
    op.drop_table("answers")
    op.drop_table("jobs")
    op.drop_table("chunks")
    op.drop_table("index_generations")
    op.drop_table("document_versions")
    op.drop_table("documents")
    op.drop_table("sources")
