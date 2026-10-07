"""Crawl behavior: scope, depth, budget, robots rules, and when removals are trusted."""

from uuid import uuid4

import pytest
from app.domain.errors import FetchFailure, IngestionFailure
from app.domain.knowledge import ClaimedJob, WebsiteTarget
from app.parsers.html import HtmlPageReader
from app.services.ingestion.crawler import CrawlPolicy, SiteCrawler
from app.services.ports.web import FetchedPage
from support import FixtureWeb

START = "https://docs.test/guide/"
ROBOTS = "https://docs.test/robots.txt"


def target(start_url=START):
    return WebsiteTarget(uuid4(), uuid4(), start_url)


def job_for(website):
    ids = (website.workspace_id, website.source_id)
    return ClaimedJob(uuid4(), "source.sync_requested", *ids, None, None, 1, 1, 5)


class Store:
    def __init__(self, website=None, live=True):
        self.target = website or target()
        self.live = live
        self.registered: list[str] = []
        self.finished = None

    def website(self, job):
        return self.target if self.live else None

    def register_page(self, job, target, spec, digest, blob_key):
        self.registered.append(spec.external_id)
        return True

    def finish(self, job, target, seen, covered):
        self.finished = (seen, covered)
        return 0


class Jobs:
    def __init__(self):
        self.cancelled = None

    def heartbeat(self, job, stage):
        pass

    def cancel(self, job, code):
        self.cancelled = code


class Blobs:
    def write(self, data):
        return uuid4().hex

    def delete(self, key):
        pass


def links(*hrefs):
    return "".join(f'<a href="{href}">x</a>' for href in hrefs)


def site(web, pages):
    for url, body in pages.items():
        web.serve(url, body)


def crawl(web, policy=CrawlPolicy(), store=None):
    store = store or Store()
    jobs = Jobs()
    SiteCrawler(jobs, store, web, HtmlPageReader(), Blobs(), policy).handle(job_for(store.target))
    return store, jobs


def failure_of(web, store=None):
    with pytest.raises(IngestionFailure) as raised:
        crawl(web, store=store)
    return (raised.value.code, raised.value.retryable)


def test_crawl_follows_in_scope_links_breadth_first_within_the_depth_limit():
    web = FixtureWeb()
    site(
        web,
        {
            START: links("a.html", "b.html", "/blog/post", "https://other.test/guide/x"),
            f"{START}a.html": links("deeper.html"),
            f"{START}b.html": "<p>B</p>",
            f"{START}deeper.html": "<p>Too deep</p>",
        },
    )
    store, _ = crawl(web, CrawlPolicy(max_pages=10, max_depth=1))
    assert store.registered == [START, f"{START}a.html", f"{START}b.html"]
    assert store.finished == (frozenset(store.registered), START)


def test_robots_rules_are_respected():
    web = FixtureWeb()
    web.serve(ROBOTS, "User-agent: *\nDisallow: /guide/private", "text/plain")
    site(web, {START: links("private.html", "public.html"), f"{START}public.html": "ok"})
    store, _ = crawl(web)
    assert (store.registered, f"{START}private.html" in web.requests) == (
        [START, f"{START}public.html"],
        False,
    )


def test_hitting_the_page_budget_leaves_the_crawl_incomplete():
    web = FixtureWeb()
    site(web, {START: links("a.html", "b.html"), f"{START}a.html": "a", f"{START}b.html": "b"})
    store, _ = crawl(web, CrawlPolicy(max_pages=2))
    assert (len(store.registered), store.finished[1]) == (2, None)


@pytest.mark.parametrize(
    ("failure", "covered"),
    [
        (FetchFailure("not_found"), START),
        (FetchFailure("fetch_timeout", True), None),
        (FetchFailure("http_error"), None),
        (FetchFailure("dns_failed"), None),
        (FetchFailure("blocked_destination"), None),
        (FetchFailure("unsupported_content"), None),
        (FetchFailure("page_too_large"), None),
        (FetchFailure("too_many_redirects"), None),
    ],
)
def test_only_pages_reported_gone_keep_the_crawl_complete(failure, covered):
    web = FixtureWeb()
    site(web, {START: links("gone.html", "kept.html"), f"{START}kept.html": "<p>Kept</p>"})
    web.fail(f"{START}gone.html", failure)
    store, _ = crawl(web)
    assert store.finished == (frozenset({START, f"{START}kept.html"}), covered)


def test_a_lone_start_page_never_retires_other_pages():
    web = FixtureWeb()
    site(web, {START: "<p>Down for maintenance</p>"})
    store, _ = crawl(web)
    assert store.finished == (frozenset({START}), None)


@pytest.mark.parametrize(
    ("failure", "retryable"),
    [
        (FetchFailure("blocked_destination"), False),
        (FetchFailure("dns_failed"), False),
        (FetchFailure("fetch_timeout", True), True),
    ],
)
def test_a_failing_start_page_fails_the_job_with_its_safe_code(failure, retryable):
    web = FixtureWeb()
    web.fail(START, failure)
    assert failure_of(web) == (failure.code, retryable)


def test_the_start_pages_final_address_sets_the_scope():
    web = FixtureWeb()
    web.pages["http://docs.test/guide"] = FetchedPage(START, "text/html", links("a.html").encode())
    site(web, {f"{START}a.html": links("../outside.html", "b.html"), f"{START}b.html": "b"})
    store, _ = crawl(web, store=Store(target("http://docs.test/guide")))
    assert store.registered == [START, f"{START}a.html", f"{START}b.html"]
    assert store.finished[1] == START


@pytest.mark.parametrize(
    ("final_url", "body"),
    [("https://sso.test/login", b"<p>Sign in</p>"), (f"{START}moved.html", b"")],
    ids=["redirected-out-of-scope", "empty"],
)
def test_unrecorded_pages_leave_the_crawl_incomplete(final_url, body):
    web = FixtureWeb()
    site(web, {START: links("moved.html", "kept.html"), f"{START}kept.html": "<p>Kept</p>"})
    web.pages[f"{START}moved.html"] = FetchedPage(final_url, "text/html", body)
    store, _ = crawl(web)
    assert (store.registered, store.finished[1]) == ([START, f"{START}kept.html"], None)


def test_deleted_sources_cancel_the_crawl_without_fetching():
    web = FixtureWeb()
    store, jobs = crawl(web, store=Store(live=False))
    assert (jobs.cancelled, web.requests) == ("obsolete", [])


def test_a_start_page_disallowed_by_robots_fails_without_being_fetched():
    web = FixtureWeb()
    web.serve(ROBOTS, "User-agent: *\nDisallow: /", "text/plain")
    assert (failure_of(web), START in web.requests) == (("robots_disallowed", False), False)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (FetchFailure("http_error", True), ("robots_unreachable", True)),
        (FetchFailure("fetch_timeout", True), ("robots_unreachable", True)),
    ],
)
def test_unreachable_robots_rules_forbid_the_crawl_until_a_retry(failure, expected):
    web = FixtureWeb()
    site(web, {START: "<p>Guide</p>"})
    web.fail(ROBOTS, failure)
    assert (failure_of(web), START in web.requests) == (expected, False)


def test_a_missing_robots_file_permits_crawling():
    web = FixtureWeb()
    site(web, {START: links("a.html"), f"{START}a.html": "a"})
    store, _ = crawl(web)
    assert store.registered == [START, f"{START}a.html"]


def test_a_crawl_that_records_nothing_fails_instead_of_retiring_everything():
    web = FixtureWeb()
    site(web, {START: ""})
    assert failure_of(web) == ("no_pages", False)
