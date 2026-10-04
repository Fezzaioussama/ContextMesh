"""Opaque cursor parsing; query scope always comes from trusted identity."""

import base64
import json
from datetime import datetime
from uuid import UUID

from app.core.exceptions import invalid_input
from app.domain.models import Conversation, Message, Page


def decode_cursor(cursor: str | None, scope: str) -> tuple[datetime, UUID] | None:
    if cursor is None:
        return None
    try:
        return _parse_cursor(cursor, scope)
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise invalid_input("The pagination cursor is invalid.") from None


def _parse_cursor(cursor: str, scope: str) -> tuple[datetime, UUID]:
    value = json.loads(base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True))
    collection, timestamp, identifier = _cursor_fields(value)
    if collection != scope:
        raise ValueError("wrong collection")
    stamp = datetime.fromisoformat(timestamp)
    if stamp.tzinfo is None:
        raise ValueError("timezone required")
    return stamp, UUID(identifier)


def _cursor_fields(value: object) -> tuple[str, str, str]:
    if not isinstance(value, dict):
        raise ValueError("cursor must be an object")
    return (_string_field(value, "scope"), _string_field(value, "time"), _string_field(value, "id"))


def _string_field(value: dict, name: str) -> str:
    field = value[name]
    if not isinstance(field, str):
        raise ValueError("cursor fields must be strings")
    return field


def encode_cursor(item: Conversation | Message, scope: str) -> str:
    value = {"scope": scope, "time": item.created_at.isoformat(), "id": str(item.id)}
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode()


def page[T: Conversation | Message](items: list[T], limit: int, scope: str) -> Page[T]:
    visible = items[:limit]
    cursor = None
    if len(items) > limit:
        cursor = encode_cursor(visible[-1], scope)
    return Page(tuple(visible), cursor)
