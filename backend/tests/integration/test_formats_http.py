"""Uploaded PDF and Office files are indexed with page, slide, sheet, or section locators."""

import pytest
from documents import docx, pdf, pptx, xlsx
from support import ask, create_source, documents, drain

pytestmark = pytest.mark.integration


def upload_bytes(client, source_id, filename, content):
    files = {"file": (filename, content, "application/octet-stream")}
    return client.post(f"/api/v1/sources/{source_id}/documents", files=files)


def first_citation(client, conversation_id, question):
    answer = ask(client, conversation_id, question).json()["assistant_message"]["answer"]
    return answer["citations"][0]


def test_pdf_answers_cite_the_page(client, worker, conversation_id):
    source_id = create_source(client)
    upload_bytes(client, source_id, "policy.pdf", pdf(["Introduction", "Keys rotate monthly."]))
    drain(worker)
    citation = first_citation(client, conversation_id, "How often do keys rotate monthly?")
    assert (citation["title"], citation["locator"]["page"], citation["snippet"]) == (
        "policy.pdf",
        2,
        "Keys rotate monthly.",
    )


@pytest.mark.parametrize(
    ("filename", "content", "question", "locator"),
    [
        (
            "handbook.docx",
            docx(),
            "Who approves new accounts?",
            {"heading_path": ["Security handbook", "Access"], "page": None, "slide": None},
        ),
        (
            "launch.pptx",
            pptx(),
            "When does the beta open?",
            {"heading_path": ["Launch plan"], "page": None, "slide": 1},
        ),
        (
            "budget.xlsx",
            xlsx(),
            "What do servers cost?",
            {"heading_path": ["Budget"], "page": None, "slide": None},
        ),
    ],
)
def test_office_documents_cite_their_section_slide_or_sheet(
    client, worker, conversation_id, filename, content, question, locator
):
    source_id = create_source(client)
    upload_bytes(client, source_id, filename, content)
    drain(worker)
    cited = first_citation(client, conversation_id, question)["locator"]
    assert {key: cited[key] for key in locator} == locator


def test_scanned_pdfs_fail_with_ocr_required(client, worker):
    source_id = create_source(client)
    upload_bytes(client, source_id, "scan.pdf", pdf(["", ""]))
    drain(worker)
    [listed] = documents(client, source_id)
    assert (
        listed["searchable"],
        listed["latest_job"]["status"],
        listed["latest_job"]["error_code"],
    ) == (
        False,
        "failed",
        "ocr_required",
    )
