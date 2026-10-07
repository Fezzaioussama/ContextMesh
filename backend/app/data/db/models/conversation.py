"""Canonical PostgreSQL tables for scoped conversations and fenced turns."""

from sqlalchemy import (
    CheckConstraint,
    Column,
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

from app.data.db.base import metadata

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
