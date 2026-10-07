"""Source and upload input policy enforced before any blob or database write."""

import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

from app.core.exceptions import invalid_input
from app.domain.errors import payload_too_large, unsupported_media_type

MEDIA_TYPES = {".md": "text/markdown", ".markdown": "text/markdown", ".txt": "text/plain"}
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class UploadSpec:
    title: str
    external_id: str
    media_type: str
    byte_size: int


def normalized_source_name(name: str) -> str:
    value = " ".join(name.split())
    if not value:
        raise invalid_input("A source name cannot be blank.")
    if len(value) > 100:
        raise invalid_input("Source names are limited to 100 characters.")
    return value


def normalized_description(description: str) -> str:
    value = " ".join(description.split())
    if len(value) > 500:
        raise invalid_input("Source descriptions are limited to 500 characters.")
    return value


def checked_upload(filename: str, byte_size: int, max_bytes: int) -> UploadSpec:
    title = _base_name(filename)
    media_type = _media_type(title)
    _checked_size(byte_size, max_bytes)
    return UploadSpec(title, title.casefold(), media_type, byte_size)


def _base_name(filename: str) -> str:
    name = PurePosixPath(PureWindowsPath(filename).name).name.strip()
    if not name or CONTROL_CHARACTERS.search(name):
        raise invalid_input("The file name is invalid.")
    if len(name.casefold()) > 200:
        raise invalid_input("File names are limited to 200 characters.")
    return name


def _media_type(name: str) -> str:
    media_type = MEDIA_TYPES.get(PurePosixPath(name).suffix.casefold())
    if media_type is None:
        raise unsupported_media_type()
    return media_type


def _checked_size(byte_size: int, max_bytes: int) -> None:
    if byte_size == 0:
        raise invalid_input("The file is empty.")
    if byte_size > max_bytes:
        raise payload_too_large(max_bytes)
