"""Website sources: registration, bounded crawl, cited page URLs, and safe retirement."""

import pytest
from app.domain.errors import FetchFailure
from app.services.ports.web import FetchedPage
from support import ask, documents, drain

pytestmark = pytest.mark.integration
START = "https://docs.test/guide/"


def create_website(client, url=START):
    body = {"name": "Product docs", "description": "Public guide", "url": url}
    return client.post("/api/v1/sources", json=body)


def publish_site(web, pages):
    for path, body in pages.items():
        web.serve(f"{START}{path}", body)


def sync(client, worker, source_id):
    accepted = client.post(f"/api/v1/sources/{source_id}/sync")
    drain(worker)
    return accepted


def page_uris(client, source_id):
    return sorted(item["uri"] for item in documents(client, source_id))


GUIDE = {
    "": '<title>Guide</title><h1>Guide</h1><p>Start here.</p><a href="install.html">Install</a>'
    '<a href="faq.html">FAQ</a>',
    "install.html": "<title>Install</title><h1>Install</h1><p>Run the installer with sudo.</p>",
    "faq.html": "<title>FAQ</title><h1>FAQ</h1><p>Answers to common questions.</p>",
}
ALL_PAGES = [START, f"{START}faq.html", f"{START}install.html"]


def test_website_crawl_indexes_pages_and_answers_cite_the_page_url(
    client, worker, web, conversation_id
):
    publish_site(web, GUIDE)
    source = create_website(client).json()
    accepted = sync(client, worker, source["id"])
    citation = ask(client, conversation_id, "How do I run the installer?").json()[
        "assistant_message"
    ]["answer"]["citations"][0]
    listed = client.get("/api/v1/sources").json()["items"][0]
    assert (accepted.status_code, source["kind"], source["url"]) == (202, "website", START)
    assert (listed["searchable_count"], listed["latest_sync"]["status"]) == (3, "succeeded")
    assert (citation["title"], citation["source_url"]) == ("Install", f"{START}install.html")


def test_pages_removed_from_the_site_are_retired_after_a_complete_crawl(client, worker, web):
    publish_site(web, GUIDE)
    source_id = create_website(client).json()["id"]
    sync(client, worker, source_id)
    web.serve(START, '<title>Guide</title><p>Install was removed.</p><a href="faq.html">FAQ</a>')
    del web.pages[f"{START}install.html"]
    sync(client, worker, source_id)
    assert page_uris(client, source_id) == [START, f"{START}faq.html"]


def test_a_crawl_retires_nothing_outside_the_part_of_the_site_it_covered(client, worker, web):
    publish_site(web, GUIDE)
    source_id = create_website(client).json()["id"]
    sync(client, worker, source_id)
    moved = "https://docs.test/v2/"
    web.pages[START] = FetchedPage(moved, "text/html", b'<p>New guide</p><a href="a.html">A</a>')
    web.serve(f"{moved}a.html", "<p>Page A</p>")
    sync(client, worker, source_id)
    latest = client.get("/api/v1/sources").json()["items"][0]["latest_sync"]
    assert (page_uris(client, source_id), latest["stage"]) == (
        sorted([*ALL_PAGES, moved, f"{moved}a.html"]),
        "crawled",
    )


def test_an_incomplete_crawl_never_retires_pages(client, worker, web, engine):
    publish_site(web, GUIDE)
    source_id = create_website(client).json()["id"]
    sync(client, worker, source_id)
    web.fail(f"{START}install.html", FetchFailure("http_error"))
    sync(client, worker, source_id)
    latest = client.get("/api/v1/sources").json()["items"][0]["latest_sync"]
    assert (page_uris(client, source_id), latest["stage"]) == (ALL_PAGES, "crawled_partial")


def test_repeated_sync_requests_share_one_open_crawl(client, web):
    publish_site(web, GUIDE)
    source_id = create_website(client).json()["id"]
    first = client.post(f"/api/v1/sources/{source_id}/sync").json()["job_id"]
    second = client.post(f"/api/v1/sources/{source_id}/sync").json()["job_id"]
    assert first == second


def test_source_kinds_reject_the_other_kinds_operations(client):
    website_id = create_website(client).json()["id"]
    upload_id = client.post("/api/v1/sources", json={"name": "Files"}).json()["id"]
    files = {"file": ("a.md", b"# A\n\nText", "text/markdown")}
    uploaded = client.post(f"/api/v1/sources/{website_id}/documents", files=files)
    synced = client.post(f"/api/v1/sources/{upload_id}/sync")
    codes = [response.json()["error"]["code"] for response in (uploaded, synced)]
    assert ([uploaded.status_code, synced.status_code], codes) == (
        [409, 409],
        ["wrong_source_kind", "wrong_source_kind"],
    )


@pytest.mark.parametrize(
    "url", ["ftp://docs.test/", "https://user:pass@docs.test/", "https://docs.test:8443/"]
)
def test_unsupported_start_urls_are_rejected_at_registration(client, url):
    response = create_website(client, url)
    assert (response.status_code, response.json()["error"]["code"]) == (422, "invalid_input")
