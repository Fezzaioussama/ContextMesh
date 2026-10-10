"""Bounded same-site crawl: fetch, version, and retire pages only after a complete scan.

Only a page answering 404/410 is known to be gone. Any other failure (blocked, 403,
DNS, timeout, too large), an empty page, or a redirect out of scope says nothing about
the page, so the crawl becomes partial and retires nothing.
"""

from collections import deque
from dataclasses import dataclass
from hashlib import sha256
from urllib.robotparser import RobotFileParser

from app.data.blobs import FilesystemBlobStore
from app.data.fetcher import SafeHttpFetcher
from app.data.indexing import CrawlRepository
from app.data.jobs import JobRepository
from app.services.models import ClaimedJob, FetchedPage, UploadSpec, WebsiteTarget
from app.services.urls import CrawlScope, crawl_scope, page_title, without_query
from app.utils.errors import FetchFailure, IngestionFailure
from app.utils.logging import flow_event
from app.utils.parsers import summarize_page

USER_AGENT = "ContextMeshBot/0.3"


@dataclass(frozen=True)
class CrawlPolicy:
    max_pages: int = 50
    max_depth: int = 2


class SiteCrawler:
    def __init__(
        self,
        jobs: JobRepository,
        store: CrawlRepository,
        fetcher: SafeHttpFetcher,
        blobs: FilesystemBlobStore,
        policy: CrawlPolicy = CrawlPolicy(),
    ):
        self._jobs = jobs
        self._store = store
        self._fetcher = fetcher
        self._blobs = blobs
        self._policy = policy

    def handle(self, job: ClaimedJob) -> None:
        target = self._store.website(job)
        if target is None:
            self._jobs.cancel(job, "obsolete")
            flow_event("crawl_website", "job_cancelled", code="obsolete")
            return
        seen, covered = self._crawl(job, target)
        if not seen:
            raise IngestionFailure("no_pages")
        self._store.finish(job, target, frozenset(seen), covered)
        flow_event("crawl_website", "crawled", pages=len(seen), complete=bool(covered))

    def _crawl(self, job: ClaimedJob, target: WebsiteTarget) -> tuple[set[str], str | None]:
        """The pages recorded, and the URL prefix this crawl proves complete (or None)."""
        robots = RobotsRules(self._fetcher)
        # The start page is fetched first: its final address (http→https, an added '/')
        # sets the scope.
        if not robots.allows(target.start_url):
            raise IngestionFailure("robots_disallowed")
        start = self._fetcher.fetch(target.start_url)
        if not robots.allows(start.url):
            raise IngestionFailure("robots_disallowed")
        scope = crawl_scope(start.url)
        frontier = Frontier(start.url, scope, self._policy)
        seen: set[str] = set()
        complete = True
        self._accept(job, target, start, 0, frontier, seen)
        while (item := frontier.next()) is not None:
            self._jobs.heartbeat(job, "crawling")
            url, depth = item
            if not robots.allows(url):
                continue
            try:
                page = self._fetcher.fetch(url)
            except FetchFailure as failure:
                complete = complete and failure.code == "not_found"
                flow_event(
                    "crawl_website", "page_skipped", url=without_query(url), code=failure.code
                )
                continue
            if not (scope.contains(page.url) and page.content):
                complete = False
                continue
            self._accept(job, target, page, depth, frontier, seen)
        # A lone page may be a maintenance or challenge page, so it never retires others.
        proven = complete and len(seen) > 1 and frontier.exhausted
        return seen, scope.root if proven else None

    def _accept(
        self,
        job: ClaimedJob,
        target: WebsiteTarget,
        page: FetchedPage,
        depth: int,
        frontier: "Frontier",
        seen: set[str],
    ) -> None:
        """Record the page as a document version (unless already seen) and queue its links."""
        summary = summarize_page(page)
        if page.url not in seen and page.content:
            seen.add(page.url)
            title = (summary.title or page_title(page.url))[:200]
            spec = UploadSpec(title, page.url, page.media_type, len(page.content), page.url)
            key = self._blobs.write(page.content)
            digest = sha256(page.content).hexdigest()
            recorded = self._store.register_page(job, target, spec, digest, key)
            if not recorded:  # Unchanged content keeps no extra blob.
                self._blobs.delete(key)
            step = "page_recorded" if recorded else "page_unchanged"
            flow_event("crawl_website", step, url=without_query(page.url))
        frontier.extend(summary.links, depth)


class Frontier:
    """Breadth-first queue after the start page, limited to scope, depth, and page budget."""

    def __init__(self, start_url: str, scope: CrawlScope, policy: CrawlPolicy):
        self._scope = scope
        self._policy = policy
        self._queue: deque[tuple[str, int]] = deque()
        self._queued = {start_url}
        self._visited = 1  # The start page is fetched before the frontier exists.

    @property
    def exhausted(self) -> bool:
        return not self._queue

    def next(self) -> tuple[str, int] | None:
        if self.exhausted or self._visited >= self._policy.max_pages:
            return None
        self._visited += 1
        return self._queue.popleft()

    def extend(self, links: tuple[str, ...], depth: int) -> None:
        if depth >= self._policy.max_depth:
            return
        for link in links:
            if link not in self._queued and self._scope.contains(link):
                self._queued.add(link)
                self._queue.append((link, depth + 1))


class RobotsRules:
    """robots.txt per origin, read once per crawl, as RFC 9309 describes.

    A missing file (404, other 4xx) permits everything. An unreachable one (5xx, network
    failure) forbids everything: the crawl fails and is retried instead of guessing.
    """

    def __init__(self, fetcher: SafeHttpFetcher):
        self._fetcher = fetcher
        self._rules: dict[str, RobotFileParser] = {}

    def allows(self, url: str) -> bool:
        origin = crawl_scope(url).origin
        if origin not in self._rules:
            rules = RobotFileParser()
            rules.parse(self._lines(origin))
            self._rules[origin] = rules
        return self._rules[origin].can_fetch(USER_AGENT, url)

    def _lines(self, origin: str) -> list[str]:
        try:
            page = self._fetcher.fetch(f"{origin}/robots.txt")
        except FetchFailure as failure:
            if failure.retryable:
                raise IngestionFailure("robots_unreachable", retryable=True) from None
            return []
        return page.content.decode("utf-8", "replace").splitlines()
