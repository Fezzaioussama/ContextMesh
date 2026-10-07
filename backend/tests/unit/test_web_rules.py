"""URL rules: what a website source accepts, how URLs compare, and what a crawl covers."""

import pytest
from app.core.exceptions import ContextMeshError
from app.domain.web import (
    canonical_url,
    checked_start_url,
    crawl_scope,
    page_title,
    resolved_link,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTPS://Docs.Example.com/Guide/Intro#setup", "https://docs.example.com/Guide/Intro"),
        ("http://example.com:80", "http://example.com/"),
        ("https://example.com:443/a?b=1", "https://example.com/a?b=1"),
        ("https://b\u00fccher.de/docs/", "https://xn--bcher-kva.de/docs/"),
        (
            "https://example.com/r\u00e9sum\u00e9 1.html?q=\u00e9",
            "https://example.com/r%C3%A9sum%C3%A9%201.html?q=%C3%A9",
        ),
        ("https://example.com/a%20b/", "https://example.com/a%20b/"),
        ("https://example.com/docs/../admin/", "https://example.com/admin/"),
        ("https://example.com/docs/%2E%2e/admin", "https://example.com/admin"),
        ("https://example.com/a/./b/..", "https://example.com/a/"),
        ("https://example.com/..", "https://example.com/"),
    ],
)
def test_urls_are_canonical(raw, expected):
    assert canonical_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "ftp://example.com/file",
        "javascript:alert(1)",
        "https://user:secret@example.com/",
        "https://example.com:8443/",
        "https:///missing-host",
        "https://bad..host/",
        "https://exa mple.com/",
        "https://exa_mple.com/",
        "not a url",
    ],
)
def test_unsupported_start_urls_are_rejected(raw):
    with pytest.raises(ContextMeshError) as failure:
        checked_start_url(raw)
    assert failure.value.code == "invalid_input"


def test_scope_is_the_start_pages_directory_on_the_same_origin():
    scope = crawl_scope("https://docs.example.com/guide/intro.html")
    decisions = [
        scope.contains(url)
        for url in (
            "https://docs.example.com/guide/setup.html",
            "https://docs.example.com/guide/deep/page",
            "https://docs.example.com/blog/post",
            "http://docs.example.com/guide/setup.html",
            "https://evil.example.com/guide/setup.html",
        )
    ]
    assert decisions == [True, True, False, False, False]


def test_dot_segments_cannot_escape_the_scope():
    scope = crawl_scope("https://docs.example.com/guide/")
    escaped = resolved_link(
        "https://docs.example.com/guide/", "https://docs.example.com/guide/../admin/"
    )
    assert (escaped, scope.contains(escaped)) == ("https://docs.example.com/admin/", False)


def test_links_resolve_relative_paths_and_skip_assets_and_other_schemes():
    base = "https://docs.example.com/guide/intro.html"
    links = [
        resolved_link(base, href)
        for href in (
            "setup.html#top",
            "../img/logo.png",
            "mailto:a@b.c",
            "  /guide/faq  ",
            "r\u00e9sum\u00e9.html",
        )
    ]
    assert links == [
        "https://docs.example.com/guide/setup.html",
        None,
        None,
        "https://docs.example.com/guide/faq",
        "https://docs.example.com/guide/r%C3%A9sum%C3%A9.html",
    ]


def test_page_titles_fall_back_to_the_last_path_segment_or_host():
    assert (
        page_title("https://example.com/docs/setup.html"),
        page_title("https://example.com/"),
        page_title("https://example.com/r%C3%A9sum%C3%A9.html"),
    ) == ("setup.html", "example.com", "r\u00e9sum\u00e9.html")
