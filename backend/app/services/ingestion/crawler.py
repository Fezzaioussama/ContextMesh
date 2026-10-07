"""Bounded same-site crawl: fetch, version, and retire pages only after a complete scan."""

from collections import deque
from dataclasses import dataclass, field
from hashlib import sha256
from urllib.robotparser import RobotFileParser

from app.services.ports.ingestion import JobQueue
from app.services.ports.sources import BlobStore
from app.services.ports.web import CrawlStore, FetchedPage, PageReader, PageSummary, WebFetcher
from app.services.rules.errors import FetchFailure, IngestionFailure
from app.services.rules.knowledge import ClaimedJob, WebsiteTarget
from app.services.rules.uploads import UploadSpec
from app.services.rules.web import CrawlScope, crawl_scope, page_title
from app.utils.logging import flow_event

USER_AGENT = "ContextMeshBot/0.3"
GONE = "not_found"


@dataclass(frozen=True)
class CrawlPolicy:
    max_pages: int = 50
    max_depth: int = 2


@dataclass
class CrawlProgress:
    """Pages recorded this run, and whether the scan can prove that the others are gone.

    Only a page answering 404/410 is known to be gone. Any other failure (blocked, 403,
    DNS, timeout, too large) says nothing about the page, so the scan becomes incomplete.
    """

    seen: set[str] = field(default_factory=set)
    complete: bool = True

    def skipped(self, failure: FetchFailure) -> None:
        if failure.code != GONE:
            self.complete = False

    @property
    def authoritative(self) -> bool:
        """A lone page may be a maintenance or challenge page, so it never retires others."""
        return self.complete and len(self.seen) > 1


class Frontier:
    """Breadth-first queue after the start page, limited to scope, depth, and page budget."""

    def __init__(self, start_url: str, scope: CrawlScope, policy: CrawlPolicy):
        self.scope = scope
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
            self._add(link, depth + 1)

    def _add(self, link: str, depth: int) -> None:
        if link in self._queued or not self.scope.contains(link):
            return
        self._queued.add(link)
        self._queue.append((link, depth))


class RobotsRules:
    """robots.txt per origin, read once per crawl, as RFC 9309 describes.

    A missing file (404, other 4xx) permits everything. An unreachable one (5xx, network
    failure) forbids everything: the crawl fails and is retried instead of guessing.
    """

    def __init__(self, fetcher: WebFetcher):
        self._fetcher = fetcher
        self._rules: dict[str, RobotFileParser] = {}

    def allows(self, url: str) -> bool:
        origin = crawl_scope(url).origin
        if origin not in self._rules:
            self._rules[origin] = self._read(origin)
        return self._rules[origin].can_fetch(USER_AGENT, url)

    def _read(self, origin: str) -> RobotFileParser:
        rules = RobotFileParser()
        rules.parse(self._lines(origin))
        return rules

    def _lines(self, origin: str) -> list[str]:
        try:
            page = self._fetcher.fetch(f"{origin}/robots.txt")
        except FetchFailure as failure:
            if failure.retryable:
                raise IngestionFailure("robots_unreachable", retryable=True) from None
            return []
        return page.content.decode("utf-8", "replace").splitlines()


@dataclass
class CrawlRun:
    """One crawl job: its rules, queue, and progress."""

    job: ClaimedJob
    target: WebsiteTarget
    robots: RobotsRules
    frontier: Frontier
    progress: CrawlProgress = field(default_factory=CrawlProgress)

    def recordable(self, page: FetchedPage) -> bool:
        """An empty page, or a redirect out of scope (e.g. to SSO), proves nothing."""
        if self.frontier.scope.contains(page.url) and page.content:
            return True
        self.progress.complete = False
        return False

    def covered(self) -> str | None:
        """The URL prefix this scan proves complete; None when it proves nothing."""
        proven = self.progress.authoritative and self.frontier.exhausted
        return self.frontier.scope.root if proven else None


class SiteCrawler:
    def __init__(
        self,
        jobs: JobQueue,
        store: CrawlStore,
        fetcher: WebFetcher,
        reader: PageReader,
        blobs: BlobStore,
        policy: CrawlPolicy = CrawlPolicy(),
    ):
        self._jobs = jobs
        self._store = store
        self._fetcher = fetcher
        self._reader = reader
        self._blobs = blobs
        self._policy = policy

    def handle(self, job: ClaimedJob) -> None:
        target = self._store.website(job)
        if target is None:
            self._jobs.cancel(job, "obsolete")
            return
        run = self._crawl(job, target)
        if not run.progress.seen:
            raise IngestionFailure("no_pages")
        covered = run.covered()
        self._store.finish(job, target, frozenset(run.progress.seen), covered)
        pages = len(run.progress.seen)
        flow_event("crawl_website", "crawled", job_id=job.id, pages=pages, complete=bool(covered))

    def _crawl(self, job: ClaimedJob, target: WebsiteTarget) -> CrawlRun:
        robots = RobotsRules(self._fetcher)
        start = self._start_page(target, robots)
        scope = crawl_scope(start.url)
        run = CrawlRun(job, target, robots, Frontier(start.url, scope, self._policy))
        self._accept(run, start, 0)
        while (item := run.frontier.next()) is not None:
            self._jobs.heartbeat(job, "crawling")
            self._visit(run, item)
        return run

    def _start_page(self, target: WebsiteTarget, robots: RobotsRules) -> FetchedPage:
        """Fetched first: its final address (http→https, an added '/') sets the scope."""
        if not robots.allows(target.start_url):
            raise IngestionFailure("robots_disallowed")
        try:
            page = self._fetcher.fetch(target.start_url)
        except FetchFailure as failure:
            raise IngestionFailure(failure.code, failure.retryable) from None
        if not robots.allows(page.url):
            raise IngestionFailure("robots_disallowed")
        return page

    def _visit(self, run: CrawlRun, item: tuple[str, int]) -> None:
        url, depth = item
        if not run.robots.allows(url):
            return
        page = self._fetch(url, run.progress)
        if page is None or not run.recordable(page):
            return
        self._accept(run, page, depth)

    def _fetch(self, url: str, progress: CrawlProgress) -> FetchedPage | None:
        try:
            return self._fetcher.fetch(url)
        except FetchFailure as failure:
            progress.skipped(failure)
            flow_event("crawl_website", "page_skipped", url=url, code=failure.code)
            return None

    def _accept(self, run: CrawlRun, page: FetchedPage, depth: int) -> None:
        summary = self._reader.summarize(page)
        self._record(run, page, summary)
        run.frontier.extend(summary.links, depth)

    def _record(self, run: CrawlRun, page: FetchedPage, summary: PageSummary) -> None:
        if page.url in run.progress.seen or not page.content:
            return
        run.progress.seen.add(page.url)
        spec = UploadSpec(
            _title(summary, page), page.url, page.media_type, len(page.content), page.url
        )
        key = self._blobs.write(page.content)
        digest = sha256(page.content).hexdigest()
        if not self._store.register_page(run.job, run.target, spec, digest, key):
            self._blobs.delete(key)
        flow_event("crawl_website", "page_recorded", job_id=run.job.id, url=page.url)


def _title(summary: PageSummary, page: FetchedPage) -> str:
    return (summary.title or page_title(page.url))[:200]
