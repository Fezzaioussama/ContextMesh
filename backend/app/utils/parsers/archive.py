"""Bound Office (ZIP) documents before a library expands them into memory."""

import zipfile
from dataclasses import dataclass
from io import BytesIO

from app.services.rules.errors import IngestionFailure


@dataclass(frozen=True)
class ArchiveLimits:
    max_entries: int = 10_000
    max_uncompressed_bytes: int = 200_000_000
    max_ratio: int = 200


def checked_archive(content: bytes, limits: ArchiveLimits) -> None:
    """Declared sizes bound what zipfile will later read, so they are safe to check."""
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
    except zipfile.BadZipFile:
        raise IngestionFailure("invalid_document") from None
    _check(entries, limits, len(content))


def _check(entries: list[zipfile.ZipInfo], limits: ArchiveLimits, size: int) -> None:
    total = sum(entry.file_size for entry in entries)
    ceiling = min(limits.max_uncompressed_bytes, max(size, 1) * limits.max_ratio)
    if len(entries) > limits.max_entries or total > ceiling:
        raise IngestionFailure("document_too_complex")
