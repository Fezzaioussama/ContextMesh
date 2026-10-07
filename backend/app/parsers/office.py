"""Word, PowerPoint, and Excel parsers; archives are bounded before they are opened."""

import re
from io import BytesIO
from itertools import zip_longest
from zipfile import BadZipFile

from docx import Document
from docx.table import Table
from openpyxl import load_workbook
from pptx import Presentation

from app.domain.errors import IngestionFailure
from app.domain.knowledge import Element
from app.parsers.archive import ArchiveLimits, checked_archive
from app.parsers.elements import HeadingTrail, cells_text, paragraphs

HEADING_STYLE = re.compile(r"^(?:heading|titre|überschrift)\s*(\d)$", re.IGNORECASE)
OPEN_ERRORS = (BadZipFile, KeyError, ValueError, TypeError)


def _opened(factory, content: bytes, limits: ArchiveLimits):
    checked_archive(content, limits)
    try:
        return factory(BytesIO(content))
    except OPEN_ERRORS:
        raise IngestionFailure("invalid_document") from None


class DocxParser:
    """Paragraph styles give the heading path; lines count body blocks in order."""

    def __init__(self, limits: ArchiveLimits = ArchiveLimits()):
        self._limits = limits

    def parse(self, content: bytes) -> tuple[Element, ...]:
        document = _opened(Document, content, self._limits)
        reader = _DocxReader()
        for position, block in enumerate(document.iter_inner_content(), start=1):
            reader.add(position, block)
        return tuple(reader.elements)


class _DocxReader:
    def __init__(self) -> None:
        self._trail = HeadingTrail()
        self.elements: list[Element] = []

    def add(self, position: int, block) -> None:
        if isinstance(block, Table):
            self._append(position, "\n".join(_row(row.cells) for row in block.rows))
            return
        self._paragraph(position, block)

    def _paragraph(self, position: int, paragraph) -> None:
        text = " ".join(paragraph.text.split())
        level = _heading_level(paragraph)
        if level and text:
            self._trail.enter(level, text)
            return
        self._append(position, text)

    def _append(self, position: int, text: str) -> None:
        if text.strip():
            self.elements.append(Element(text, self._trail.path, position, position))


def _heading_level(paragraph) -> int:
    name = paragraph.style.name if paragraph.style is not None else ""
    if name.casefold() == "title":
        return 1
    match = HEADING_STYLE.match(name)
    return int(match.group(1)) if match else 0


def _row(cells) -> str:
    return cells_text(list(dict.fromkeys(" ".join(cell.text.split()) for cell in cells)))


class PptxParser:
    """Each slide's title is its heading; lines count paragraphs within the slide."""

    def __init__(self, limits: ArchiveLimits = ArchiveLimits()):
        self._limits = limits

    def parse(self, content: bytes) -> tuple[Element, ...]:
        presentation = _opened(Presentation, content, self._limits)
        return tuple(
            element
            for number, slide in enumerate(presentation.slides, start=1)
            for element in _slide(slide, number)
        )


def _slide(slide, number: int) -> list[Element]:
    title = _title(slide)
    lines = [*_body(slide), *_notes(slide)]
    return paragraphs(lines, (title,) if title else (), slide=number)


def _title(slide) -> str:
    shape = slide.shapes.title
    return " ".join(shape.text.split()) if shape is not None else ""


def _body(slide) -> list[str]:
    title = slide.shapes.title
    return _shapes_lines([shape for shape in slide.shapes if shape != title])


def _shapes_lines(shapes) -> list[str]:
    return [line for shape in shapes for line in _lines(shape)]


def _lines(shape) -> list[str]:
    """Text frames and tables become blocks; group shapes contribute their children."""
    if shape.has_text_frame:
        return _frame_lines(shape.text_frame)
    if shape.has_table:
        return _table_lines(shape.table)
    return _shapes_lines(getattr(shape, "shapes", ()))


def _frame_lines(frame) -> list[str]:
    return [paragraph.text for paragraph in frame.paragraphs] + [""]


def _table_lines(table) -> list[str]:
    return [_row(row.cells) for row in table.rows] + [""]


def _notes(slide) -> list[str]:
    if not slide.has_notes_slide:
        return []
    return ["", *slide.notes_slide.notes_text_frame.text.splitlines()]


class XlsxParser:
    """One section per sheet; rows are labelled with the sheet's first non-empty row."""

    def __init__(self, limits: ArchiveLimits = ArchiveLimits(), max_rows: int = 20_000):
        self._limits = limits
        self._max_rows = max_rows

    def parse(self, content: bytes) -> tuple[Element, ...]:
        workbook = _opened(_workbook, content, self._limits)
        try:
            return tuple(element for sheet in workbook.worksheets for element in self._sheet(sheet))
        finally:
            workbook.close()

    def _sheet(self, sheet) -> list[Element]:
        rows = enumerate(sheet.iter_rows(values_only=True), start=1)
        header: list[str] = []
        elements: list[Element] = []
        for number, values in rows:
            if number > self._max_rows:
                raise IngestionFailure("document_too_large")
            header = header or _cells(values)
            elements.extend(_row_element(sheet.title, number, header, _cells(values)))
        return elements


def _workbook(stream):
    return load_workbook(stream, read_only=True, data_only=True)


def _cells(values) -> list[str]:
    return ["" if value is None else " ".join(str(value).split()) for value in values]


def _row_element(sheet: str, number: int, header: list[str], cells: list[str]) -> list[Element]:
    text = _labelled(header, cells) if cells != header else cells_text(cells)
    return [Element(text, (sheet,), number, number)] if text else []


def _labelled(header: list[str], cells: list[str]) -> str:
    pairs = zip_longest(header, cells, fillvalue="")
    return " | ".join(f"{name}: {value}" if name else value for name, value in pairs if value)
