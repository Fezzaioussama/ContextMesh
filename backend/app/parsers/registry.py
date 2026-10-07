"""Parser per stored media type; the upload policy decides which types are accepted."""

from app.domain.uploads import DOCX, PDF, PPTX, XLSX
from app.parsers.blocks import BlockParser
from app.parsers.html import HtmlParser
from app.parsers.office import DocxParser, PptxParser, XlsxParser
from app.parsers.pdf import PdfParser
from app.services.ports.ingestion import Parser


def default_parsers() -> dict[str, Parser]:
    return {
        "text/markdown": BlockParser(markdown=True),
        "text/plain": BlockParser(markdown=False),
        "text/html": HtmlParser(),
        PDF: PdfParser(),
        DOCX: DocxParser(),
        PPTX: PptxParser(),
        XLSX: XlsxParser(),
    }
