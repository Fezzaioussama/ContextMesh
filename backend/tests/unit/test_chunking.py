"""Chunking keeps sections apart, bounds size, and derives stable chunk identities."""

import pytest
from app.domain.chunking import ChunkingPolicy, chunk_elements, estimate_tokens
from app.domain.errors import IngestionFailure
from app.domain.knowledge import Element
from app.parsers.blocks import BlockParser

MARKDOWN = b"""# Platform handbook

Intro paragraph about the platform.

## Authentication

We use OIDC.

```text
# not a heading inside a fence
```

### Rollout

Billing goes first.
"""


def test_markdown_paragraphs_keep_heading_path_and_line_range():
    elements = BlockParser(markdown=True).parse(MARKDOWN)
    assert [
        (element.heading_path, element.line_start, element.line_end) for element in elements
    ] == [
        (("Platform handbook",), 3, 3),
        (("Platform handbook", "Authentication"), 7, 7),
        (("Platform handbook", "Authentication"), 9, 11),
        (("Platform handbook", "Authentication", "Rollout"), 15, 15),
    ]
    assert "# not a heading inside a fence" in elements[2].text


def test_sibling_heading_replaces_the_previous_branch():
    elements = BlockParser(markdown=True).parse(b"# A\n## B\nfirst\n## C\nsecond\n# D\nthird\n")
    assert [element.heading_path for element in elements] == [("A", "B"), ("A", "C"), ("D",)]


def test_plain_text_never_interprets_markdown_headings():
    elements = BlockParser(markdown=False).parse(b"# literal\nline two\n\nNext paragraph")
    assert [(element.text, element.heading_path) for element in elements] == [
        ("# literal\nline two", ()),
        ("Next paragraph", ()),
    ]


@pytest.mark.parametrize("content", [b"\xff\xfe broken", b"text\x00with nul"])
def test_undecodable_or_binary_content_is_an_explicit_failure(content):
    with pytest.raises(IngestionFailure) as failure:
        BlockParser(markdown=True).parse(content)
    assert failure.value.code == "invalid_encoding"
    assert failure.value.retryable is False


def test_byte_order_mark_is_not_indexed():
    assert BlockParser(markdown=False).parse(b"\xef\xbb\xbfHello").__getitem__(0).text == "Hello"


LONG_PARAGRAPH = " ".join(f"word{index}" for index in range(55))


def element(text, path=("Section",), line=1):
    return Element(text, path, line, line)


def test_sections_never_share_a_chunk():
    chunks = chunk_elements(
        (element("alpha", ("A",)), element("beta", ("B",))), ChunkingPolicy(), "seed"
    )
    assert [chunk.locator.heading_path for chunk in chunks] == [("A",), ("B",)]


def test_chunks_respect_the_token_bound_and_split_long_paragraphs():
    policy = ChunkingPolicy(max_tokens=20, overlap_tokens=5)
    long_paragraph = LONG_PARAGRAPH
    chunks = chunk_elements((element(long_paragraph),), policy, "seed")
    largest = max(chunk.token_count for chunk in chunks)
    rejoined = " ".join(chunk.text for chunk in chunks)
    assert (len(chunks), largest <= policy.max_tokens, rejoined) == (3, True, long_paragraph)


def test_small_trailing_paragraph_is_carried_as_overlap():
    policy = ChunkingPolicy(max_tokens=12, overlap_tokens=4)
    first = element("one two three four five six", line=1)
    bridge = element("bridge words", line=2)
    third = element("seven eight nine ten eleven", line=3)
    chunks = chunk_elements((first, bridge, third), policy, "seed")
    assert [chunk.text for chunk in chunks] == [
        "one two three four five six\n\nbridge words",
        "bridge words\n\nseven eight nine ten eleven",
    ]
    assert (chunks[1].locator.line_start, chunks[1].locator.line_end) == (2, 3)


def test_chunk_identity_is_deterministic_for_the_same_seed_only():
    elements = (element("stable text"),)
    first = chunk_elements(elements, ChunkingPolicy(), "doc:v1:pipeline")
    replay = chunk_elements(elements, ChunkingPolicy(), "doc:v1:pipeline")
    other_version = chunk_elements(elements, ChunkingPolicy(), "doc:v2:pipeline")
    assert first[0].id == replay[0].id
    assert first[0].id != other_version[0].id


UNSPACED = "鍵" * 5000


def test_unspaced_text_is_bounded_by_characters_not_only_estimated_tokens():
    policy = ChunkingPolicy(max_chars=1000)
    chunks = chunk_elements((element(UNSPACED),), policy, "seed")
    longest = max(len(chunk.text) for chunk in chunks)
    rejoined = "".join(chunk.text.replace(" ", "") for chunk in chunks)
    assert (len(chunks), longest, rejoined) == (5, 1000, UNSPACED)


def test_document_with_too_many_chunks_fails_without_partial_output():
    policy = ChunkingPolicy(max_tokens=5, overlap_tokens=0, max_chunks=2)
    elements = tuple(element(f"para {index}", (f"H{index}",)) for index in range(3))
    with pytest.raises(IngestionFailure) as failure:
        chunk_elements(elements, policy, "seed")
    assert failure.value.code == "document_too_large"


def test_embedding_text_includes_heading_context_but_stored_text_does_not():
    chunk = chunk_elements((element("Body", ("Guide", "Setup")),), ChunkingPolicy(), "seed")[0]
    assert chunk.text == "Body"
    assert chunk.embedding_text == "Guide > Setup\n\nBody"


def test_token_estimate_counts_words_and_punctuation():
    assert estimate_tokens("Hello, world!") == 4
