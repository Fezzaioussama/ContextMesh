"""Input rules enforced for HTTP and direct callers alike, before anything is stored."""

import re
from pathlib import PurePosixPath, PureWindowsPath
from uuid import UUID

from app.services.models import UploadSpec
from app.utils.errors import ContextMeshError

MAX_MESSAGE_CHARS = 8000
MAX_SOURCE_FILTER = 50
PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TEXT_EXTENSIONS = (
    ".txt .text .log .rst .adoc .csv .tsv .json .jsonl .xml .yaml .yml .toml .ini .cfg "
    ".conf .sql .py .js .jsx .ts .tsx .java .kt .go .rs .c .h .cpp .hpp .cs .rb .php "
    ".swift .scala .sh .ps1 .css .scss .graphql .proto .tex .srt .vtt"
).split()
MEDIA_TYPES = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".pdf": PDF,
    ".docx": DOCX,
    ".pptx": PPTX,
    ".xlsx": XLSX,
    **dict.fromkeys(TEXT_EXTENSIONS, "text/plain"),
}
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


def invalid(message: str) -> ContextMeshError:
    return ContextMeshError("invalid_input", message)


def normalized_message(message: str) -> str:
    text = message.strip()
    if not text:
        raise invalid("Enter a nonempty message.")
    if len(text) > MAX_MESSAGE_CHARS:
        raise invalid(f"Messages are limited to {MAX_MESSAGE_CHARS} characters.")
    return text


def checked_key(key: str) -> str:
    if not 1 <= len(key) <= 128:
        raise invalid("Idempotency-Key must contain 1–128 characters.")
    return key


def normalized_title(title: str) -> str:
    value = title.strip()
    if not value:
        raise invalid("A title cannot be blank.")
    if len(value) > 100:
        raise invalid("Titles are limited to 100 characters.")
    return value


def checked_source_filter(source_ids: tuple[UUID, ...] | None) -> tuple[UUID, ...] | None:
    if source_ids is None:
        return None
    unique = tuple(dict.fromkeys(source_ids))
    if not 1 <= len(unique) <= MAX_SOURCE_FILTER:
        raise invalid("Select between 1 and 50 sources, or omit the filter.")
    return unique


def checked_page(limit: int, maximum: int, cursor: str | None) -> None:
    if not 1 <= limit <= maximum:
        raise invalid("The page limit is invalid.")
    if cursor is not None and len(cursor) > 2048:
        raise invalid("The pagination cursor is invalid.")


def normalized_source_name(name: str) -> str:
    value = " ".join(name.split())
    if not value:
        raise invalid("A source name cannot be blank.")
    if len(value) > 100:
        raise invalid("Source names are limited to 100 characters.")
    return value


def normalized_description(description: str) -> str:
    value = " ".join(description.split())
    if len(value) > 500:
        raise invalid("Source descriptions are limited to 500 characters.")
    return value


def checked_upload(filename: str, byte_size: int, max_bytes: int) -> UploadSpec:
    """Checks name, type, and size; the title is the base name without any path."""
    title = PurePosixPath(PureWindowsPath(filename).name).name.strip()
    if not title or CONTROL_CHARACTERS.search(title):
        raise invalid("The file name is invalid.")
    if len(title.casefold()) > 200:
        raise invalid("File names are limited to 200 characters.")
    media_type = MEDIA_TYPES.get(PurePosixPath(title).suffix.casefold())
    if media_type is None:
        raise ContextMeshError("unsupported_media_type")
    if byte_size == 0:
        raise invalid("The file is empty.")
    if byte_size > max_bytes:
        raise too_large(max_bytes)
    return UploadSpec(title, title.casefold(), media_type, byte_size)


def too_large(limit: int) -> ContextMeshError:
    return ContextMeshError("payload_too_large", f"Uploads are limited to {limit} bytes.")
