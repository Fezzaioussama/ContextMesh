"""Text-bearing PDFs, one locator per page; pages without text need OCR."""

from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from app.services.rules.errors import IngestionFailure
from app.services.rules.knowledge import Element
from app.utils.parsers.elements import paragraphs

READ_ERRORS = (PyPdfError, ValueError, KeyError, TypeError)


class PdfParser:
    def __init__(self, max_pages: int = 1000):
        self._max_pages = max_pages

    def parse(self, content: bytes) -> tuple[Element, ...]:
        pages = _pages(content)
        if len(pages) > self._max_pages:
            raise IngestionFailure("document_too_large")
        elements = _elements(pages)
        if not elements:
            raise IngestionFailure("ocr_required")
        return elements


def _elements(pages: list) -> tuple[Element, ...]:
    return tuple(element for number, page in enumerate(pages, 1) for element in _page(page, number))


def _pages(content: bytes) -> list:
    if not content.startswith(b"%PDF"):
        raise IngestionFailure("invalid_document")
    try:
        reader = PdfReader(BytesIO(content))
        _unlock(reader)
        return list(reader.pages)
    except READ_ERRORS:
        raise IngestionFailure("invalid_document") from None


def _unlock(reader: PdfReader) -> None:
    """Owner-password-only PDFs open with an empty user password; others cannot."""
    if reader.is_encrypted and not reader.decrypt(""):
        raise IngestionFailure("encrypted_document")


def _page(page, number: int) -> list[Element]:
    try:
        text = page.extract_text() or ""
    except READ_ERRORS:
        return []
    return paragraphs(text.splitlines(), (), page=number)
