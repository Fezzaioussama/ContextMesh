"""Canonical requester and membership checks shared by repository operations."""

from uuid import UUID

from sqlalchemy import Connection, RowMapping, and_, select
from sqlalchemy.sql.elements import ColumnElement

from app.data.db.models.conversation import conversations
from app.data.db.models.user import memberships
from app.services.rules.models import Conversation, Message
from app.utils.exceptions import unavailable_resource
from app.utils.security import Identity


def require_membership(connection: Connection, identity: Identity) -> None:
    query = select(memberships.c.subject).where(
        memberships.c.workspace_id == identity.workspace_id,
        memberships.c.subject == identity.subject,
    )
    if connection.execute(query).first() is None:
        raise unavailable_resource()


def scoped_conversations(identity: Identity) -> ColumnElement[bool]:
    return and_(
        conversations.c.workspace_id == identity.workspace_id,
        conversations.c.subject == identity.subject,
    )


def require_conversation(
    connection: Connection, identity: Identity, conversation_id: UUID, lock: bool = False
) -> RowMapping:
    require_membership(connection, identity)
    query = select(conversations).where(
        scoped_conversations(identity), conversations.c.id == conversation_id
    )
    if lock:
        query = query.with_for_update()
    row = connection.execute(query).mappings().first()
    if row is None:
        raise unavailable_resource()
    return row


def conversation_value(row: RowMapping) -> Conversation:
    return Conversation(row["id"], row["title"], row["created_at"], row["updated_at"])


def message_value(row: RowMapping) -> Message:
    return Message(row["id"], row["turn_id"], row["role"], row["content"], row["created_at"])
