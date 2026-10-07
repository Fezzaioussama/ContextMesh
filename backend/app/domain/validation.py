"""Input policy enforced for HTTP and direct application callers alike."""

from uuid import UUID

from app.core.exceptions import invalid_input

MAX_SOURCE_FILTER = 50


def normalized_message(message: str) -> str:
    text = message.strip()
    if not text:
        raise invalid_input("Enter a nonempty message.")
    if len(text) > 8000:
        raise invalid_input("Messages are limited to 8000 characters.")
    return text


def checked_key(key: str) -> str:
    if not 1 <= len(key) <= 128:
        raise invalid_input("Idempotency-Key must contain 1–128 characters.")
    return key


def normalized_title(title: str) -> str:
    value = title.strip()
    if not value:
        raise invalid_input("A title cannot be blank.")
    if len(value) > 100:
        raise invalid_input("Titles are limited to 100 characters.")
    return value


def checked_source_filter(source_ids: tuple[UUID, ...] | None) -> tuple[UUID, ...] | None:
    if source_ids is None:
        return None
    unique = tuple(dict.fromkeys(source_ids))
    if not 1 <= len(unique) <= MAX_SOURCE_FILTER:
        raise invalid_input("Select between 1 and 50 sources, or omit the filter.")
    return unique


def checked_page(limit: int, maximum: int, cursor: str | None) -> None:
    if not 1 <= limit <= maximum:
        raise invalid_input("The page limit is invalid.")
    if cursor is not None and len(cursor) > 2048:
        raise invalid_input("The pagination cursor is invalid.")
