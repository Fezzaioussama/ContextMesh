"""Each format keeps a usable location; malformed or textless input fails explicitly."""

import time
import zipfile
from io import BytesIO

import pytest
from app.services.ports.web import FetchedPage, PageSummary
from app.services.rules.errors import IngestionFailure
from app.utils.parsers.archive import ArchiveLimits
from app.utils.parsers.html import HtmlPageReader, HtmlParser
from app.utils.parsers.office import DocxParser, PptxParser, XlsxParser
from app.utils.parsers.pdf import PdfParser
from documents import docx, pdf, pptx, xlsx


def located(elements):
    return [(item.text, item.heading_path, item.page, item.slide) for item in elements]


def test_pdf_text_keeps_page_numbers():
    elements = PdfParser().parse(pdf(["Overview\nKeys rotate monthly.", "Appendix"]))
    assert located(elements) == [
        ("Overview\nKeys rotate monthly.", (), 1, None),
        ("Appendix", (), 2, None),
    ]


def test_pdf_without_a_text_layer_needs_ocr():
    with pytest.raises(IngestionFailure) as failure:
        PdfParser().parse(pdf(["", ""]))
    assert (failure.value.code, failure.value.retryable) == ("ocr_required", False)


@pytest.mark.parametrize("content", [b"not a pdf", b"%PDF-1.4 truncated"])
def test_malformed_pdf_is_an_invalid_document(content):
    with pytest.raises(IngestionFailure) as failure:
        PdfParser().parse(content)
    assert failure.value.code == "invalid_document"


def test_pdf_page_limit_is_enforced():
    with pytest.raises(IngestionFailure) as failure:
        PdfParser(max_pages=1).parse(pdf(["one", "two"]))
    assert failure.value.code == "document_too_large"


def test_word_headings_and_tables_keep_their_section():
    elements = DocxParser().parse(docx())
    assert located(elements) == [
        ("Rotate keys every quarter.", ("Security handbook",), None, None),
        ("Admins approve new accounts.", ("Security handbook", "Access"), None, None),
        ("Role | Owner\nAdmin | Platform team", ("Security handbook", "Access"), None, None),
    ]


def test_slides_keep_slide_numbers_titles_and_notes():
    elements = PptxParser().parse(pptx())
    assert located(elements) == [
        ("Beta opens in May.", ("Launch plan",), None, 1),
        ("Mention the waitlist.", ("Launch plan",), None, 1),
        ("Capacity is limited.", ("Risks",), None, 2),
    ]


def test_sheet_rows_are_labelled_with_the_header_row():
    elements = XlsxParser().parse(xlsx())
    assert [(item.text, item.heading_path, item.line_start) for item in elements] == [
        ("Item | Cost", ("Budget",), 1),
        ("Item: Servers | Cost: 1200", ("Budget",), 2),
        ("Item: Licenses | Cost: 300", ("Budget",), 4),
    ]


def oversized_archive() -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"0" * 200_000)
    return buffer.getvalue()


def test_archives_that_expand_too_far_are_rejected_before_parsing():
    with pytest.raises(IngestionFailure) as failure:
        DocxParser(ArchiveLimits(max_ratio=10)).parse(oversized_archive())
    assert failure.value.code == "document_too_complex"


@pytest.mark.parametrize("parser", [DocxParser(), PptxParser(), XlsxParser()])
def test_non_archives_are_invalid_office_documents(parser):
    with pytest.raises(IngestionFailure) as failure:
        parser.parse(b"plain text pretending to be office")
    assert failure.value.code == "invalid_document"


HTML = b"""<html><head><title> Guide | Docs </title><script>var hidden = 1;</script></head>
<body><nav><a href="/elsewhere">Menu</a></nav>
<h1>Install</h1>
<p>Run the <b>installer</b>.</p>
<ul><li><p>Nested once</p></li></ul>
<h2>Configure</h2>
<div>Set the token.</div>
<pre>line one
line two</pre>
<footer>Copyright</footer>
</body></html>"""


def test_html_blocks_keep_headings_and_source_lines_without_boilerplate():
    elements = HtmlParser().parse(HTML)
    assert [(item.text, item.heading_path, item.line_start) for item in elements] == [
        ("Run the installer.", ("Install",), 4),
        ("Nested once", ("Install",), 5),
        ("Set the token.", ("Install", "Configure"), 7),
        ("line one\nline two", ("Install", "Configure"), 8),
    ]


def test_html_block_discovery_stays_linear_on_crafted_nesting():
    """Per-tag ancestor searches made these shapes quadratic: tens of seconds at 50 KB."""
    leaves = "<div>t</div>" * 4000
    nested = "<div>" * 2000 + "<p>deep</p>" + "</div>" * 2000
    body = f"<body><div><span>{leaves}</span><p>a</p></div>{nested}</body>".encode()
    started = time.perf_counter()
    elements = HtmlParser().parse(body)
    elapsed = time.perf_counter() - started
    assert (len(elements), [item.text for item in elements[-2:]], elapsed < 2) == (
        4002,
        ["a", "deep"],
        True,
    )


def test_page_reader_returns_title_and_resolved_document_links():
    body = (
        b'<title>Docs</title><a href="guide/a.html#top">A</a><a href="/logo.png">x</a>'
        b'<a href="mailto:x@y.z">m</a><a href="guide/a.html">dup</a>'
    )
    summary = HtmlPageReader().summarize(
        FetchedPage("https://docs.example.com/start/", "text/html", body)
    )
    assert (summary.title, summary.links) == (
        "Docs",
        ("https://docs.example.com/start/guide/a.html",),
    )


def test_non_html_pages_have_no_title_or_links():
    page = FetchedPage("https://example.com/a.pdf", "application/pdf", b"%PDF")
    assert HtmlPageReader().summarize(page) == PageSummary(None, ())
