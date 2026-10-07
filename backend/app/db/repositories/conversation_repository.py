"""Short scoped transactions and stable pagination over canonical history."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Engine, insert, select, tuple_

from app.core.security import Identity
from app.db.models.conversation import (
    conversations,
    messages,
)
from app.db.repositories.access import (
    conversation_value,
    message_value,
    require_conversation,
    require_membership,
    scoped_conversations,
)
from app.db.repositories.answer_records import with_answers
from app.db.repositories.paging import (
    decode_cursor,
    page,
)
from app.domain.models import Conversation, Message, Page


class ConversationRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def create(self, identity: Identity, title: str) -> Conversation:
        now = datetime.now(UTC)
        value = Conversation(uuid4(), title, now, now)
        with self.engine.begin() as connection:
            require_membership(connection, identity)
            connection.execute(
                insert(conversations).values(
                    id=value.id,
                    workspace_id=identity.workspace_id,
                    subject=identity.subject,
                    title=title,
                    created_at=now,
                    updated_at=now,
                )
            )
        return value

    def conversations(
        self, identity: Identity, limit: int, cursor: str | None
    ) -> Page[Conversation]:
        scope = f"conversations:{identity.workspace_id}:{identity.subject}"
        position = decode_cursor(cursor, scope)
        query = select(conversations).where(scoped_conversations(identity))
        if position:
            query = query.where(tuple_(conversations.c.created_at, conversations.c.id) < position)
        query = query.order_by(conversations.c.created_at.desc(), conversations.c.id.desc())
        with self.engine.begin() as connection:
            require_membership(connection, identity)
            rows = connection.execute(query.limit(limit + 1)).mappings().all()
            items = [conversation_value(row) for row in rows]
        return page(items, limit, scope)

    def messages(
        self, identity: Identity, conversation_id: UUID, limit: int, cursor: str | None
    ) -> Page[Message]:
        scope = f"messages:{identity.workspace_id}:{identity.subject}:{conversation_id}"
        position = decode_cursor(cursor, scope)
        query = select(messages).where(messages.c.conversation_id == conversation_id)
        if position:
            query = query.where(tuple_(messages.c.created_at, messages.c.id) > position)
        query = query.order_by(messages.c.created_at, messages.c.id)
        with self.engine.begin() as connection:
            require_conversation(connection, identity, conversation_id)
            rows = connection.execute(query.limit(limit + 1)).mappings().all()
            items = with_answers(connection, [message_value(row) for row in rows])
        return page(items, limit, scope)
