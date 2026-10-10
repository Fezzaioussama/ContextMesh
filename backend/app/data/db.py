"""PostgreSQL: the canonical tables, engine, readiness, and the membership check every
repository runs first. Migrations in `backend/migrations` create these tables."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Computed,
    Connection,
    DateTime,
    Engine,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    create_engine,
    inspect,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import insert as upsert
from sqlalchemy.exc import SQLAlchemyError

from app.services.models import Identity
from app.utils.errors import ContextMeshError

SCHEMA_REVISION = "0004_formats_and_websites"
SEARCH_VECTOR = "to_tsvector('english', coalesce(heading, '') || ' ' || content)"

metadata = MetaData()

# Identity

workspaces = Table("workspaces", metadata, Column("id", Uuid, primary_key=True))
memberships = Table(
    "memberships",
    metadata,
    Column("workspace_id", Uuid, ForeignKey("workspaces.id"), primary_key=True),
    Column("subject", String(200), primary_key=True),
    Column("role", String(30), nullable=False),
)

# Conversations

conversations = Table(
    "conversations",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("workspace_id", Uuid, ForeignKey("workspaces.id"), nullable=False),
    Column("subject", String(200), nullable=False),
    Column("title", String(100), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
Index(
    "ix_conversations_scope",
    conversations.c.workspace_id,
    conversations.c.subject,
    conversations.c.created_at,
    conversations.c.id,
)

turns = Table(
    "turns",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("conversation_id", Uuid, ForeignKey("conversations.id"), nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Column("status", String(20), nullable=False),
    Column("execution_token", Uuid, nullable=False),
    Column("lease_until", DateTime(timezone=True), nullable=False),
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("safe_error_code", String(50)),
    UniqueConstraint("conversation_id", "idempotency_key", name="uq_turn_key"),
    CheckConstraint("status IN ('running', 'failed', 'completed')", name="ck_turn_status"),
)
Index(
    "uq_running_turn",
    turns.c.conversation_id,
    unique=True,
    postgresql_where=text("status = 'running'"),
)

messages = Table(
    "messages",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("conversation_id", Uuid, ForeignKey("conversations.id"), nullable=False),
    Column("turn_id", Uuid, ForeignKey("turns.id"), nullable=False),
    Column("role", String(20), nullable=False),
    Column("content", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("turn_id", "role", name="uq_turn_role"),
    CheckConstraint("role IN ('user', 'assistant')", name="ck_message_role"),
)
Index("ix_messages_order", messages.c.conversation_id, messages.c.created_at, messages.c.id)

# Sources, documents, versions, index generations, chunks, and durable jobs

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

# Saved answers and their citations

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

REQUIRED_TABLES = frozenset({"alembic_version", *metadata.tables})


def create_database_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True, connect_args={"connect_timeout": 3})


def database_ready(engine: Engine) -> bool:
    """Every table exists and migrations are at the revision this code expects."""
    try:
        with engine.connect() as connection:
            if not REQUIRED_TABLES.issubset(inspect(connection).get_table_names()):
                return False
            version = connection.exec_driver_sql("SELECT version_num FROM alembic_version")
            return version.scalar() == SCHEMA_REVISION
    except SQLAlchemyError:
        return False


def provision_identity(engine: Engine, identity: Identity) -> None:
    """Make the fixed development identity the owner of its workspace."""
    with engine.begin() as connection:
        connection.execute(
            upsert(workspaces).values(id=identity.workspace_id).on_conflict_do_nothing()
        )
        connection.execute(
            upsert(memberships)
            .values(workspace_id=identity.workspace_id, subject=identity.subject, role="owner")
            .on_conflict_do_nothing()
        )


def member_role(connection: Connection, identity: Identity) -> str:
    """The requester's role in its workspace; non-members see nothing at all."""
    query = select(memberships.c.role).where(
        memberships.c.workspace_id == identity.workspace_id,
        memberships.c.subject == identity.subject,
    )
    role = connection.execute(query).scalar()
    if role is None:
        raise ContextMeshError("not_found")
    return role
