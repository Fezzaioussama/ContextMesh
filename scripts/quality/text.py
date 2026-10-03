"""Check physical lines and reject unreadable maintained text files."""

import re
from pathlib import Path

from .report import Report

MAX_LINES = 1000
BINARY_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".pdf",
        ".zip",
        ".gz",
        ".bz2",
        ".xz",
        ".7z",
        ".tar",
        ".whl",
        ".pyc",
        ".pyo",
        ".so",
        ".dylib",
        ".dll",
        ".exe",
        ".ttf",
        ".woff",
        ".woff2",
        ".eot",
        ".otf",
        ".mp3",
        ".mp4",
        ".wav",
        ".ogg",
        ".sqlite",
        ".db",
        ".docx",
        ".pptx",
        ".xlsx",
        ".bin",
    }
)


def physical_lines(text: str) -> int:
    breaks = len(re.findall(r"\r\n|\r|\n", text))
    return breaks + int(bool(text) and not text.endswith(("\r", "\n")))


def is_binary(path: Path, data: bytes) -> bool:
    if path.suffix.lower() == ".py":
        return False
    return path.suffix.lower() in BINARY_SUFFIXES or b"\0" in data


def decode_text(path: Path, data: bytes, report: Report) -> str | None:
    if is_binary(path, data):
        report.binary_files += 1
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        report.add(path, 1, "encoding", f"maintained text must be UTF-8: {error}")
        return None


def check_lines(path: Path, text: str, report: Report) -> None:
    count = physical_lines(text)
    if count > MAX_LINES:
        report.add(path, MAX_LINES + 1, "file-lines", f"{count} lines exceeds {MAX_LINES}")


def read_text(path: Path, report: Report) -> str | None:
    try:
        data = path.read_bytes()
    except OSError as error:
        report.add(path, 1, "read-error", str(error))
        return None
    return decode_text(path, data, report)
