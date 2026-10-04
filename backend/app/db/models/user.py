"""Shared identity tables and metadata for one canonical PostgreSQL schema."""

from sqlalchemy import Column, ForeignKey, String, Table, Uuid

from app.db.base import metadata

workspaces = Table("workspaces", metadata, Column("id", Uuid, primary_key=True))
memberships = Table(
    "memberships",
    metadata,
    Column("workspace_id", Uuid, ForeignKey("workspaces.id"), primary_key=True),
    Column("subject", String(200), primary_key=True),
    Column("role", String(30), nullable=False),
)
