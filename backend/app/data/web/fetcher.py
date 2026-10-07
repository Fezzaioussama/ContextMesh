"""Guarded HTTP fetcher: public destinations only, enforced at every TCP connection.

Each connection resolves the host itself, rejects the request if any resolved address
is not public, and connects to a validated address. Redirects reconnect through the
same check, so DNS rebinding and redirects to internal hosts cannot reach them. TLS
still verifies the certificate against the original hostname.

Every socket operation of one fetch (redirects included) shares one wall-clock budget:
per-operation timeouts alone let a server dribble a byte at a time indefinitely.
"""

import ipaddress
import socket
import threading
import time
import zlib
from collections.abc import Callable
from urllib.parse import urljoin, urlsplit

import httpcore

from app.services.ports.web import FetchedPage
from app.services.rules.errors import FetchFailure
from app.services.rules.uploads import PDF
from app.services.rules.web import canonical_url

USER_AGENT = "ContextMeshBot/0.3 (+https://github.com/context-mesh)"
REDIRECTS = frozenset({301, 302, 303, 307, 308})
MEDIA_TYPES = {
    "text/html": "text/html",
    "application/xhtml+xml": "text/html",
    "text/plain": "text/plain",
    "text/markdown": "text/markdown",
    "text/x-markdown": "text/markdown",
    "application/pdf": PDF,
}
GLOBAL_UNICAST = ipaddress.ip_network("2000::/3")
NAT64 = ipaddress.ip_network("64:ff9b::/96")
Resolver = Callable[[str, int], list[str]]
AddressPolicy = Callable[[str], bool]


class BlockedDestination(Exception):
    """The destination is not a public address on an allowed port."""


class UnresolvedHost(Exception):
    """DNS gave no answer; only a temporary resolver failure is worth retrying."""

    def __init__(self, retryable: bool):
        super().__init__("unresolved host")
        self.retryable = retryable


class DeadlineExceeded(httpcore.TimeoutException):
    """The fetch used up its wall-clock budget."""


def is_public_address(address: str) -> bool:
    """Globally routable unicast only; IPv6 must be global unicast (2000::/3)."""
    try:
        ip = _unwrapped(ipaddress.ip_address(address))
    except ValueError:
        return False
    return ip.is_global and not ip.is_multicast and _unicast_range(ip)


def _unwrapped(ip: ipaddress.IPv4Address | ipaddress.IPv6Address):
    """The IPv4 address an IPv6 one stands for (IPv4-mapped or NAT64), else itself."""
    if ip.version == 6 and ip in NAT64:
        return ipaddress.IPv4Address(int(ip) & 0xFFFF_FFFF)
    return getattr(ip, "ipv4_mapped", None) or ip


def _unicast_range(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return ip.version == 4 or ip in GLOBAL_UNICAST


def system_resolve(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise UnresolvedHost(error.errno == socket.EAI_AGAIN) from None
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


class Deadline:
    """The wall-clock end of the current fetch, per thread; unset means already over."""

    def __init__(self) -> None:
        self._local = threading.local()

    def start(self, seconds: float) -> None:
        self._local.ends = time.monotonic() + seconds

    def bounded(self, timeout: float | None) -> float:
        remaining = getattr(self._local, "ends", 0.0) - time.monotonic()
        if remaining <= 0:
            raise DeadlineExceeded()
        return remaining if timeout is None else min(timeout, remaining)


class DeadlineStream(httpcore.NetworkStream):
    """Caps every read, write, and TLS handshake by what is left of the fetch budget."""

    def __init__(self, inner: httpcore.NetworkStream, deadline: Deadline):
        self._inner = inner
        self._deadline = deadline

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._inner.read(max_bytes, self._deadline.bounded(timeout))

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._inner.write(buffer, self._deadline.bounded(timeout))

    def close(self) -> None:
        self._inner.close()

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        inner = self._inner.start_tls(ssl_context, server_hostname, self._deadline.bounded(timeout))
        return DeadlineStream(inner, self._deadline)

    def get_extra_info(self, info: str):
        return self._inner.get_extra_info(info)


class GuardedBackend(httpcore.NetworkBackend):
    def __init__(
        self,
        resolve: Resolver,
        allowed: AddressPolicy,
        ports: frozenset[int] | None,
        deadline: Deadline,
    ):
        self._inner = httpcore.SyncBackend()
        self._resolve = resolve
        self._allowed = allowed
        self._ports = ports
        self._deadline = deadline

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        def connect(address: str) -> httpcore.NetworkStream:
            bounded = self._deadline.bounded(timeout)
            stream = self._inner.connect_tcp(address, port, bounded, local_address, socket_options)
            return DeadlineStream(stream, self._deadline)

        return _first_connected(self._approved(host, port), connect)

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise BlockedDestination()

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)

    def _approved(self, host: str, port: int) -> list[str]:
        """Every resolved address must be allowed before any of them is contacted."""
        if not self._port_allowed(port):
            raise BlockedDestination()
        addresses = self._resolve(host, port)
        if not self._all_allowed(addresses):
            raise BlockedDestination()
        return addresses

    def _port_allowed(self, port: int) -> bool:
        return self._ports is None or port in self._ports

    def _all_allowed(self, addresses: list[str]) -> bool:
        return bool(addresses) and all(self._allowed(address) for address in addresses)


def _first_connected(addresses: list[str], connect) -> httpcore.NetworkStream:
    """Try validated addresses in order, e.g. IPv4 when a dual-stack host has no IPv6 route."""
    for address in addresses[:-1]:
        try:
            return connect(address)
        except (httpcore.ConnectError, httpcore.ConnectTimeout):
            continue
    return connect(addresses[-1])


TRANSPORT_ERRORS = (
    BlockedDestination,
    UnresolvedHost,
    httpcore.TimeoutException,
    httpcore.NetworkError,
    httpcore.ProtocolError,
    httpcore.UnsupportedProtocol,
)


class SafeHttpFetcher:
    """`timeout_seconds` caps each network operation; a whole fetch gets twice that."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        max_bytes: int = 5_000_000,
        max_redirects: int = 5,
        resolve: Resolver = system_resolve,
        address_allowed: AddressPolicy = is_public_address,
        ports: frozenset[int] | None = frozenset({80, 443}),
    ):
        self._deadline = Deadline()
        backend = GuardedBackend(resolve, address_allowed, ports, self._deadline)
        self._pool = httpcore.ConnectionPool(network_backend=backend, retries=0)
        self._timeout = timeout_seconds
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects

    def fetch(self, url: str) -> FetchedPage:
        self._deadline.start(2 * self._timeout)
        current = url
        for _ in range(self._max_redirects + 1):
            outcome = self._request(current)
            if isinstance(outcome, FetchedPage):
                return outcome
            current = outcome
        raise FetchFailure("too_many_redirects")

    def close(self) -> None:
        self._pool.close()

    def _request(self, url: str) -> FetchedPage | str:
        target, headers = _prepared(url)
        timeouts = dict.fromkeys(("connect", "read", "write", "pool"), self._timeout)
        try:
            with self._pool.stream(
                "GET", target, headers=headers, extensions={"timeout": timeouts}
            ) as response:
                return self._handle(url, response)
        except TRANSPORT_ERRORS as error:
            raise _transport_failure(error) from None

    def _handle(self, url: str, response) -> FetchedPage | str:
        if response.status in REDIRECTS:
            return _redirect_target(url, response)
        _checked_status(response.status)
        media_type = _media_type(response)
        return FetchedPage(url, media_type, self._body(response))

    def _body(self, response) -> bytes:
        encoding = _header(response, b"content-encoding")
        if encoding not in ("", "identity", "gzip"):
            raise FetchFailure("unsupported_content")
        raw = _bounded_read(response, self._max_bytes)
        return _gunzipped(raw, self._max_bytes) if encoding == "gzip" else raw


def _prepared(url: str) -> tuple[httpcore.URL, list[tuple[bytes, bytes]]]:
    """Only ASCII URLs reach the wire; anything else fails safely instead of crashing."""
    try:
        return httpcore.URL(url), _headers(url)
    except (TypeError, ValueError):
        raise FetchFailure("invalid_url") from None


def _headers(url: str) -> list[tuple[bytes, bytes]]:
    return [
        (b"Host", urlsplit(url).netloc.encode("ascii")),
        (b"User-Agent", USER_AGENT.encode()),
        (b"Accept", b"text/html,application/xhtml+xml,text/plain,text/markdown,application/pdf"),
        (b"Accept-Encoding", b"gzip, identity"),
    ]


def _header(response, name: bytes) -> str:
    return _header_raw(response, name).lower()


def _redirect_target(url: str, response) -> str:
    target = canonical_url(urljoin(url, _header_raw(response, b"location")), any_port=True)
    if target is None:
        raise FetchFailure("blocked_destination")
    return target


def _header_raw(response, name: bytes) -> str:
    values = [value for key, value in response.headers if key.lower() == name]
    return values[0].decode("latin-1").strip() if values else ""


def _checked_status(status: int) -> None:
    if not 200 <= status < 300:
        raise _status_failure(status)


def _status_failure(status: int) -> FetchFailure:
    """Gone pages are not errors; throttling and server faults are worth retrying."""
    if status in (404, 410):
        return FetchFailure("not_found")
    return FetchFailure("http_error", retryable=status == 429 or status >= 500)


def _media_type(response) -> str:
    declared = _header(response, b"content-type").split(";")[0].strip()
    if declared not in MEDIA_TYPES:
        raise FetchFailure("unsupported_content")
    return MEDIA_TYPES[declared]


def _bounded_read(response, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_stream():
        size += len(chunk)
        if size > max_bytes:
            raise FetchFailure("page_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def _gunzipped(raw: bytes, max_bytes: int) -> bytes:
    try:
        text = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(raw, max_bytes + 1)
    except zlib.error:
        raise FetchFailure("fetch_failed", retryable=True) from None
    if len(text) > max_bytes:
        raise FetchFailure("page_too_large")
    return text


def _transport_failure(error: Exception) -> FetchFailure:
    if isinstance(error, UnresolvedHost):
        return FetchFailure("dns_failed", retryable=error.retryable)
    if isinstance(error, (BlockedDestination, httpcore.UnsupportedProtocol)):
        return FetchFailure("blocked_destination")
    if isinstance(error, httpcore.TimeoutException):
        return FetchFailure("fetch_timeout", retryable=True)
    return FetchFailure("fetch_failed", retryable=True)
