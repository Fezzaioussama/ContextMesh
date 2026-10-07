"""Upload, source, and scope input rules apply before any storage or model work."""

from uuid import uuid4

import pytest
from app.core.exceptions import ContextMeshError
from app.domain.uploads import checked_upload, normalized_description, normalized_source_name
from app.domain.validation import checked_source_filter


@pytest.mark.parametrize(
    ("filename", "title", "media_type"),
    [
        ("notes.md", "notes.md", "text/markdown"),
        ("../../etc/README.MARKDOWN", "README.MARKDOWN", "text/markdown"),
        ("C:\\Users\\me\\plan.txt", "plan.txt", "text/plain"),
    ],
)
def test_upload_name_is_reduced_to_a_safe_base_name(filename, title, media_type):
    spec = checked_upload(filename, 10, 100)
    assert (spec.title, spec.media_type, spec.external_id) == (title, media_type, title.casefold())


@pytest.mark.parametrize(
    ("filename", "size", "code"),
    [
        ("report.pdf", 10, "unsupported_media_type"),
        ("notes", 10, "unsupported_media_type"),
        ("notes.md", 0, "invalid_input"),
        ("notes.md", 101, "payload_too_large"),
        ("bad\x00name.md", 10, "invalid_input"),
        ("", 10, "invalid_input"),
    ],
)
def test_invalid_uploads_are_rejected_with_specific_codes(filename, size, code):
    with pytest.raises(ContextMeshError) as failure:
        checked_upload(filename, size, 100)
    assert failure.value.code == code


def test_source_names_and_descriptions_are_normalized_and_bounded():
    assert normalized_source_name("  Team   handbook ") == "Team handbook"
    assert normalized_description("  a \n b ") == "a b"
    for invalid in ("   ", "x" * 101):
        with pytest.raises(ContextMeshError):
            normalized_source_name(invalid)
    with pytest.raises(ContextMeshError):
        normalized_description("x" * 501)


def test_omitted_filter_means_all_sources_but_empty_filter_is_invalid():
    assert checked_source_filter(None) is None
    with pytest.raises(ContextMeshError):
        checked_source_filter(())


def test_filter_is_deduplicated_and_bounded():
    source = uuid4()
    assert checked_source_filter((source, source)) == (source,)
    with pytest.raises(ContextMeshError):
        checked_source_filter(tuple(uuid4() for _ in range(51)))
