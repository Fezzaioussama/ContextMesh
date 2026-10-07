"""Markdown and plain-text parsing into paragraphs with heading paths and line ranges."""

import re
from collections.abc import Callable

from app.services.rules.errors import IngestionFailure
from app.services.rules.knowledge import Element
from app.utils.parsers.elements import HeadingTrail

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")


def decoded(content: bytes) -> str:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise IngestionFailure("invalid_encoding") from None
    if "\x00" in text:
        raise IngestionFailure("invalid_encoding")
    return text.removeprefix("﻿")


class BlockParser:
    """Markdown mode tracks ATX headings and keeps fenced code inside one block."""

    def __init__(self, markdown: bool):
        self._markdown = markdown

    def parse(self, content: bytes) -> tuple[Element, ...]:
        reader = _BlockReader(self._markdown)
        for number, line in enumerate(decoded(content).splitlines(), start=1):
            reader.feed(number, line)
        return reader.finish()


class _BlockReader:
    def __init__(self, markdown: bool):
        self._markdown = markdown
        self._trail = HeadingTrail()
        self._lines: list[str] = []
        self._start = 0
        self._end = 0
        self._fenced = False
        self._elements: list[Element] = []

    def feed(self, number: int, line: str) -> None:
        self._handler(line)(number, line)

    def finish(self) -> tuple[Element, ...]:
        self._flush()
        return tuple(self._elements)

    def _handler(self, line: str) -> Callable[[int, str], None]:
        if self._is_fence(line):
            return self._toggle_fence
        if self._fenced:
            return self._append
        return self._content if line.strip() else self._blank

    def _is_fence(self, line: str) -> bool:
        return self._markdown and FENCE.match(line) is not None

    def _toggle_fence(self, number: int, line: str) -> None:
        self._fenced = not self._fenced
        self._append(number, line)

    def _blank(self, number: int, line: str) -> None:
        self._flush()

    def _content(self, number: int, line: str) -> None:
        heading = HEADING.match(line) if self._markdown else None
        if heading is None:
            self._append(number, line)
            return
        self._flush()
        self._trail.enter(len(heading.group(1)), heading.group(2).strip())

    def _append(self, number: int, line: str) -> None:
        if not self._lines:
            self._start = number
        self._lines.append(line.rstrip())
        self._end = number

    def _flush(self) -> None:
        text = "\n".join(self._lines).strip()
        if text:
            self._elements.append(Element(text, self._trail.path, self._start, self._end))
        self._lines = []
