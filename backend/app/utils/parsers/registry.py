"""Parser per stored media type; the upload policy decides which types are accepted."""

from app.services.ports.ingestion import Parser
from app.services.rules.uploads import DOCX, PDF, PPTX, XLSX
from app.utils.parsers.blocks import BlockParser
from app.utils.parsers.html import HtmlParser
from app.utils.parsers.office import DocxParser, PptxParser, XlsxParser
from app.utils.parsers.pdf import PdfParser


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
