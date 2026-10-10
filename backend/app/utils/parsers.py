"""Document parsers: each turns file bytes into text Elements that keep their location
(heading path, line range, and page or slide), so every citation can point back.

Office files are ZIP archives and are size-checked before a library expands them.
"""

import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from itertools import zip_longest

from bs4 import BeautifulSoup, Tag
from docx import Document
from docx.table import Table
from openpyxl import load_workbook
from pptx import Presentation
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from app.services.models import Element, FetchedPage, PageSummary
from app.services.urls import resolved_link
from app.services.validation import DOCX, PDF, PPTX, XLSX
from app.utils.errors import IngestionFailure

Parser = Callable[[bytes], tuple[Element, ...]]


def parse(media_type: str, content: bytes) -> tuple[Element, ...]:
    parser = PARSERS.get(media_type)
    if parser is None:
        raise IngestionFailure("unsupported_media_type")
    return parser(content)


class HeadingTrail:
    """The current heading path; a heading replaces its level and everything below it."""

    def __init__(self) -> None:
        self._headings: list[tuple[int, str]] = []

    @property
    def path(self) -> tuple[str, ...]:
        return tuple(title for _, title in self._headings)

    def enter(self, level: int, title: str) -> None:
        while self._headings and self._headings[-1][0] >= level:
            self._headings.pop()
        self._headings.append((level, title))


def paragraphs(
    lines: list[str],
    heading_path: tuple[str, ...],
    page: int | None = None,
    slide: int | None = None,
) -> list[Element]:
    """Group consecutive non-blank lines; line numbers are 1-based within `lines`."""
    groups: list[list[tuple[int, str]]] = [[]]
    for number, line in enumerate(lines, start=1):
        text = " ".join(line.split())
        if text:
            groups[-1].append((number, text))
        elif groups[-1]:
            groups.append([])
    return [
        Element(
            "\n".join(text for _, text in group),
            heading_path,
            group[0][0],
            group[-1][0],
            page,
            slide,
        )
        for group in groups
        if group
    ]


def cells_text(values: list[str]) -> str:
    return " | ".join(value for value in values if value)


# Markdown and plain text

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")


def parse_markdown(content: bytes) -> tuple[Element, ...]:
    return _blocks(content, markdown=True)


def parse_text(content: bytes) -> tuple[Element, ...]:
    return _blocks(content, markdown=False)


def _blocks(content: bytes, markdown: bool) -> tuple[Element, ...]:
    """Paragraphs separated by blank lines. Markdown mode tracks ATX headings and keeps
    fenced code inside one block."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise IngestionFailure("invalid_encoding") from None
    if "\x00" in text:
        raise IngestionFailure("invalid_encoding")
    trail = HeadingTrail()
    elements: list[Element] = []
    block: list[tuple[int, str]] = []

    def flush() -> None:
        body = "\n".join(line for _, line in block).strip()
        if body:
            elements.append(Element(body, trail.path, block[0][0], block[-1][0]))
        block.clear()

    fenced = False
    for number, line in enumerate(text.removeprefix("﻿").splitlines(), start=1):
        heading = HEADING.match(line) if markdown and not fenced else None
        if markdown and FENCE.match(line):
            fenced = not fenced
            block.append((number, line.rstrip()))
        elif fenced or (line.strip() and heading is None):
            block.append((number, line.rstrip()))
        elif heading is not None:
            flush()
            trail.enter(len(heading.group(1)), heading.group(2).strip())
        else:
            flush()
    flush()
    return tuple(elements)


# HTML documents and crawled pages

REMOVED = ["script", "style", "noscript", "template", "svg", "iframe", "nav", "footer", "form"]
HEADINGS = {f"h{level}": level for level in range(1, 7)}
BLOCKS = frozenset(
    {*HEADINGS, "p", "li", "pre", "blockquote", "td", "th", "dd", "dt", "figcaption", "caption"}
)


def parse_html(content: bytes) -> tuple[Element, ...]:
    """Lines are the block's starting line in the original HTML source."""
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup.find_all(REMOVED):
        tag.decompose()
    trail = HeadingTrail()
    elements: list[Element] = []
    for tag in _outermost_blocks(soup.body or soup):
        text = tag.get_text().strip() if tag.name == "pre" else " ".join(tag.get_text().split())
        if not text:
            continue
        if tag.name in HEADINGS:
            trail.enter(HEADINGS[tag.name], text)
            continue
        line = tag.sourceline or 1
        elements.append(Element(text, trail.path, line, line))
    return tuple(elements)


def _outermost_blocks(root: Tag) -> list[Tag]:
    """Blocks with no block ancestor, in document order, in linear time.

    A block is a known block element, or a `div` with no known block element below it
    (a leaf `div` holding loose text). Ancestor checks per tag would be quadratic, so
    one bottom-up pass first marks every tag that holds a block.
    """
    holders: set[int] = set()
    for tag in reversed(root.find_all(True)):
        if tag.name in BLOCKS or id(tag) in holders:
            holders.add(id(tag.parent))
    found: list[Tag] = []
    pending = [root]
    while pending:
        tag = pending.pop()
        if tag.name in BLOCKS or (tag.name == "div" and id(tag) not in holders):
            found.append(tag)
        else:
            pending.extend(reversed([child for child in tag.children if isinstance(child, Tag)]))
    return found


def summarize_page(page: FetchedPage) -> PageSummary:
    """Title and in-page links for the crawler; non-HTML pages have neither."""
    if page.media_type != "text/html":
        return PageSummary(None, ())
    soup = BeautifulSoup(page.content, "html.parser")
    title = " ".join(soup.title.get_text().split()) if soup.title else ""
    links = (resolved_link(page.url, anchor["href"]) for anchor in soup.find_all("a", href=True))
    return PageSummary(title or None, tuple(dict.fromkeys(link for link in links if link)))


# PDF

PDF_ERRORS = (PyPdfError, ValueError, KeyError, TypeError)


def parse_pdf(content: bytes, max_pages: int = 1000) -> tuple[Element, ...]:
    """Text-bearing PDFs, one locator per page; pages without text need OCR."""
    if not content.startswith(b"%PDF"):
        raise IngestionFailure("invalid_document")
    try:
        reader = PdfReader(BytesIO(content))
        # Owner-password-only PDFs open with an empty user password; others cannot.
        if reader.is_encrypted and not reader.decrypt(""):
            raise IngestionFailure("encrypted_document")
        pages = list(reader.pages)
    except PDF_ERRORS:
        raise IngestionFailure("invalid_document") from None
    if len(pages) > max_pages:
        raise IngestionFailure("document_too_large")
    elements = []
    for number, page in enumerate(pages, start=1):
        try:
            text = page.extract_text() or ""
        except PDF_ERRORS:
            continue
        elements.extend(paragraphs(text.splitlines(), (), page=number))
    if not elements:
        raise IngestionFailure("ocr_required")
    return tuple(elements)


# Office: Word, PowerPoint, Excel


@dataclass(frozen=True)
class ArchiveLimits:
    max_entries: int = 10_000
    max_uncompressed_bytes: int = 200_000_000
    max_ratio: int = 200


HEADING_STYLE = re.compile(r"^(?:heading|titre|überschrift)\s*(\d)$", re.IGNORECASE)
OPEN_ERRORS = (zipfile.BadZipFile, KeyError, ValueError, TypeError)


def _opened(factory, content: bytes, limits: ArchiveLimits):
    """Declared sizes bound what zipfile will later read, so they are safe to check."""
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
    except zipfile.BadZipFile:
        raise IngestionFailure("invalid_document") from None
    total = sum(entry.file_size for entry in entries)
    ceiling = min(limits.max_uncompressed_bytes, max(len(content), 1) * limits.max_ratio)
    if len(entries) > limits.max_entries or total > ceiling:
        raise IngestionFailure("document_too_complex")
    try:
        return factory(BytesIO(content))
    except OPEN_ERRORS:
        raise IngestionFailure("invalid_document") from None


def parse_docx(content: bytes, limits: ArchiveLimits = ArchiveLimits()) -> tuple[Element, ...]:
    """Paragraph styles give the heading path; lines count body blocks in order."""
    document = _opened(Document, content, limits)
    trail = HeadingTrail()
    elements: list[Element] = []
    for position, block in enumerate(document.iter_inner_content(), start=1):
        if isinstance(block, Table):
            text = "\n".join(_row(row.cells) for row in block.rows)
        else:
            text = " ".join(block.text.split())
            level = _heading_level(block)
            if level and text:
                trail.enter(level, text)
                continue
        if text.strip():
            elements.append(Element(text, trail.path, position, position))
    return tuple(elements)


def _heading_level(paragraph) -> int:
    name = paragraph.style.name if paragraph.style is not None else ""
    if name.casefold() == "title":
        return 1
    match = HEADING_STYLE.match(name)
    return int(match.group(1)) if match else 0


def _row(cells) -> str:
    return cells_text(list(dict.fromkeys(" ".join(cell.text.split()) for cell in cells)))


def parse_pptx(content: bytes, limits: ArchiveLimits = ArchiveLimits()) -> tuple[Element, ...]:
    """Each slide's title is its heading; lines count paragraphs within the slide."""
    presentation = _opened(Presentation, content, limits)
    elements: list[Element] = []
    for number, slide in enumerate(presentation.slides, start=1):
        title_shape = slide.shapes.title
        title = " ".join(title_shape.text.split()) if title_shape is not None else ""
        lines = _shape_lines([shape for shape in slide.shapes if shape != title_shape])
        if slide.has_notes_slide:
            lines += ["", *slide.notes_slide.notes_text_frame.text.splitlines()]
        elements.extend(paragraphs(lines, (title,) if title else (), slide=number))
    return tuple(elements)


def _shape_lines(shapes) -> list[str]:
    """Text frames and tables become blocks; group shapes contribute their children."""
    lines: list[str] = []
    for shape in shapes:
        if shape.has_text_frame:
            lines += [paragraph.text for paragraph in shape.text_frame.paragraphs] + [""]
        elif shape.has_table:
            lines += [_row(row.cells) for row in shape.table.rows] + [""]
        else:
            lines += _shape_lines(getattr(shape, "shapes", ()))
    return lines


def parse_xlsx(
    content: bytes, limits: ArchiveLimits = ArchiveLimits(), max_rows: int = 20_000
) -> tuple[Element, ...]:
    """One section per sheet; rows are labelled with the sheet's first non-empty row."""
    workbook = _opened(
        lambda stream: load_workbook(stream, read_only=True, data_only=True), content, limits
    )
    elements: list[Element] = []
    try:
        for sheet in workbook.worksheets:
            header: list[str] = []
            for number, values in enumerate(sheet.iter_rows(values_only=True), start=1):
                if number > max_rows:
                    raise IngestionFailure("document_too_large")
                cells = ["" if value is None else " ".join(str(value).split()) for value in values]
                header = header or cells
                text = _labelled(header, cells) if cells != header else cells_text(cells)
                if text:
                    elements.append(Element(text, (sheet.title,), number, number))
    finally:
        workbook.close()
    return tuple(elements)


def _labelled(header: list[str], cells: list[str]) -> str:
    pairs = zip_longest(header, cells, fillvalue="")
    return " | ".join(f"{name}: {value}" if name else value for name, value in pairs if value)


PARSERS: dict[str, Parser] = {
    "text/markdown": parse_markdown,
    "text/plain": parse_text,
    "text/html": parse_html,
    PDF: parse_pdf,
    DOCX: parse_docx,
    PPTX: parse_pptx,
    XLSX: parse_xlsx,
}
