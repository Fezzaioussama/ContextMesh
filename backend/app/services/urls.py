"""Website URL rules: start-URL validation, canonical URLs, and the bounded crawl scope."""

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from app.utils.errors import ContextMeshError

SCHEMES = frozenset({"http", "https"})
DEFAULT_PORTS = {"http": 80, "https": 443}
MAX_URL_LENGTH = 2000
URL_SAFE = "/?:@!$&'()*+,;=%"  # Reserved characters and existing escapes stay as written.
ENCODED_DOT = re.compile("%2e", re.IGNORECASE)
HOST = re.compile(r"[a-z0-9.\-]+|[0-9a-f:.]+")  # A DNS name or an IPv4/IPv6 literal.
SKIPPED_EXTENSIONS = frozenset(
    ".png .jpg .jpeg .gif .svg .webp .ico .bmp .css .js .mjs .map .zip .gz .tgz .tar "
    ".rar .7z .mp3 .mp4 .webm .mov .avi .wav .woff .woff2 .ttf .otf .eot .exe .dmg "
    ".iso .bin .apk .msi".split()
)


@dataclass(frozen=True)
class CrawlScope:
    """Same scheme and host as the start page, under the start page's directory."""

    origin: str
    prefix: str

    @property
    def root(self) -> str:
        """Every canonical URL inside the scope starts with this string."""
        return f"{self.origin}{self.prefix}"

    def contains(self, url: str) -> bool:
        parts = urlsplit(url)
        same_origin = f"{parts.scheme}://{parts.netloc}" == self.origin
        return same_origin and parts.path.startswith(self.prefix)


def checked_start_url(url: str) -> str:
    canonical = canonical_url(url.strip())
    if canonical is None or len(canonical) > MAX_URL_LENGTH:
        raise ContextMeshError(
            "invalid_input",
            "Enter a public http(s) URL without credentials or a custom port.",
        )
    return canonical


def crawl_scope(start_url: str) -> CrawlScope:
    parts = urlsplit(start_url)
    prefix = parts.path[: parts.path.rfind("/") + 1] or "/"
    return CrawlScope(f"{parts.scheme}://{parts.netloc}", prefix)


def canonical_url(url: str, any_port: bool = False) -> str | None:
    """ASCII URL: lowercase scheme/host, IDNA host, percent-encoded path and query,
    dot segments removed, no credentials or fragment, and default ports unless `any_port`.

    The fetcher passes `any_port` for redirects because its connection guard enforces
    ports itself, after resolving and checking the destination address.
    """
    try:
        parts = urlsplit(url)
        port = parts.port
        host = (parts.hostname or "").encode("idna").decode("ascii")
    except ValueError:  # Also UnicodeError, for invalid internationalized labels.
        return None
    default_port = port is None or port == DEFAULT_PORTS.get(parts.scheme)
    if parts.scheme not in SCHEMES or parts.username is not None or not HOST.fullmatch(host):
        return None
    if not (default_port or any_port):
        return None
    netloc = f"[{host}]" if ":" in host else host
    if not default_port:
        netloc = f"{netloc}:{port}"
    path = _without_dot_segments(quote(parts.path or "/", safe=URL_SAFE))
    return urlunsplit((parts.scheme, netloc, path, quote(parts.query, safe=URL_SAFE), ""))


def _without_dot_segments(path: str) -> str:
    """RFC 3986 section 5.2.4, so `..` cannot step outside the crawl directory."""
    segments = ENCODED_DOT.sub(".", path).split("/")[1:]
    kept: list[str] = []
    for segment in segments:
        if segment == "..":
            del kept[-1:]
        elif segment != ".":
            kept.append(segment)
    if segments[-1] in (".", ".."):
        kept.append("")
    return "/" + "/".join(kept)


def resolved_link(base: str, href: str) -> str | None:
    """Canonical absolute link, or None for non-web and non-document targets."""
    canonical = canonical_url(urljoin(base, href.strip()))
    if canonical is None or len(canonical) > MAX_URL_LENGTH:
        return None
    suffix = PurePosixPath(urlsplit(canonical).path).suffix.casefold()
    return None if suffix in SKIPPED_EXTENSIONS else canonical


def page_title(url: str) -> str:
    parts = urlsplit(url)
    name = unquote(PurePosixPath(parts.path).name) or parts.hostname or url
    return name[:200]


def without_query(url: str) -> str:
    """For logs: the query string may carry tokens, so only scheme, host, and path."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
