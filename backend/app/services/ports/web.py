"""Crawl ports: guarded page fetching, page summaries, and fenced crawl persistence."""

from dataclasses import dataclass
from typing import Protocol

from app.services.rules.knowledge import ClaimedJob, WebsiteTarget
from app.services.rules.uploads import UploadSpec


@dataclass(frozen=True)
class FetchedPage:
    url: str
    media_type: str
    content: bytes


@dataclass(frozen=True)
class PageSummary:
    title: str | None
    links: tuple[str, ...]


class WebFetcher(Protocol):
    """Fetch one public URL; raise FetchFailure with a safe code otherwise."""

    def fetch(self, url: str) -> FetchedPage: ...


class PageReader(Protocol):
    def summarize(self, page: FetchedPage) -> PageSummary: ...


class CrawlStore(Protocol):
    def website(self, job: ClaimedJob) -> WebsiteTarget | None: ...

    def register_page(
        self, job: ClaimedJob, target: WebsiteTarget, spec: UploadSpec, digest: str, blob_key: str
    ) -> bool: ...

    def finish(
        self, job: ClaimedJob, target: WebsiteTarget, seen: frozenset[str], covered: str | None
    ) -> int:
        """Succeed the crawl; retire unseen pages whose URL starts with `covered`, if any."""
        ...
