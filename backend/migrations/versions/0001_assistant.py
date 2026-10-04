"""Canonical development identity, conversations and fenced model executions."""

import sqlalchemy as sa
from alembic import op

revision = "0001_assistant"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    create_identity_tables()
    create_conversations()
    create_turns()
    create_messages()


def create_identity_tables():
    op.create_table("workspaces", sa.Column("id", sa.Uuid(), primary_key=True))
    op.create_table(
        "memberships",
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id"), primary_key=True),
        sa.Column("subject", sa.String(200), primary_key=True),
        sa.Column("role", sa.String(30), nullable=False),
    )


def create_conversations():
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_conversations_scope", "conversations", ["workspace_id", "subject", "created_at", "id"]
    )


def create_turns():
    op.create_table(
        "turns",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("execution_token", sa.Uuid(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("safe_error_code", sa.String(50)),
        sa.UniqueConstraint("conversation_id", "idempotency_key", name="uq_turn_key"),
        sa.CheckConstraint("status IN ('running', 'failed', 'completed')", name="ck_turn_status"),
    )
    op.create_index(
        "uq_running_turn",
        "turns",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )


def create_messages():
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("turn_id", sa.Uuid(), sa.ForeignKey("turns.id"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("turn_id", "role", name="uq_turn_role"),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_message_role"),
    )
    op.create_index("ix_messages_order", "messages", ["conversation_id", "created_at", "id"])


def downgrade():
    op.drop_table("messages")
    op.drop_table("turns")
    op.drop_table("conversations")
    op.drop_table("memberships")
    op.drop_table("workspaces")
