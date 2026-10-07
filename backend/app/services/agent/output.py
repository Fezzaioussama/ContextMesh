"""Validate structured model output; malformed fields become a safe model failure."""

from collections.abc import Mapping
from uuid import UUID

from app.services.rules.errors import model_output_invalid
from app.services.rules.knowledge import CatalogSource


def text_field(data: Mapping[str, object], name: str, limit: int) -> str:
    value = data.get(name)
    if not isinstance(value, str):
        raise model_output_invalid()
    return _compact(value, limit)


def flag(data: Mapping[str, object], name: str) -> bool:
    value = data.get(name)
    if not isinstance(value, bool):
        raise model_output_invalid()
    return value


def string_list(
    data: Mapping[str, object], name: str, max_items: int, max_length: int
) -> tuple[str, ...]:
    return _distinct(_clean(_list(data, name), max_length))[:max_items]


def objects(
    data: Mapping[str, object], name: str, max_items: int
) -> tuple[Mapping[str, object], ...]:
    return tuple(item for item in _list(data, name) if isinstance(item, dict))[:max_items]


def known_sources(values: tuple[str, ...], sources: tuple[CatalogSource, ...]) -> tuple[UUID, ...]:
    allowed = {str(source.id): source.id for source in sources}
    return tuple(dict.fromkeys(allowed[key] for key in _keys(values) if key in allowed))


def _keys(values: tuple[str, ...]) -> list[str]:
    return [value.strip().lower() for value in values]


def _list(data: Mapping[str, object], name: str) -> list:
    value = data.get(name)
    if not isinstance(value, list):
        raise model_output_invalid()
    return value


def _clean(values: list, max_length: int) -> list[str]:
    return [_compact(item, max_length) for item in values if isinstance(item, str)]


def _distinct(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for item in values if item))


def _compact(value: str, limit: int) -> str:
    return " ".join(value.split())[:limit]
