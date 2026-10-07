"""The guarded fetcher against a real local HTTP server and a controlled resolver."""

import gzip
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from app.domain.errors import FetchFailure
from app.web.fetcher import SafeHttpFetcher, UnresolvedHost, is_public_address, system_resolve

PUBLIC, INTERNAL, CLOSED = "127.0.0.1", "127.0.0.2", "127.0.0.3"
HOSTS = {
    "public.test": [PUBLIC],
    "internal.test": [INTERNAL],
    "mixed.test": [PUBLIC, INTERNAL],
    "dual.test": [CLOSED, PUBLIC],
}


def fixture_resolve(host: str, port: int) -> list[str]:
    if host not in HOSTS:
        raise UnresolvedHost(retryable=False)
    return HOSTS[host]


def routes(port: int) -> dict:
    return {
        "/page": (200, {"Content-Type": "text/html; charset=utf-8"}, b"<p>Hello</p>"),
        "/to-internal": (302, {"Location": f"http://internal.test:{port}/page"}, b""),
        "/to-ftp": (302, {"Location": "ftp://public.test/file"}, b""),
        "/loop": (302, {"Location": "/loop"}, b""),
        "/large": (200, {"Content-Type": "text/plain"}, b"x" * 5000),
        "/zipped": (
            200,
            {"Content-Type": "text/plain", "Content-Encoding": "gzip"},
            gzip.compress(b"compressed body"),
        ),
        "/image": (200, {"Content-Type": "image/png"}, b"\x89PNG"),
        "/error": (503, {"Content-Type": "text/plain"}, b"busy"),
    }


class Handler(BaseHTTPRequestHandler):
    hosts: list[list[str]] = []

    def do_GET(self):
        Handler.hosts.append(self.headers.get_all("Host"))
        default = (404, {"Content-Type": "text/plain"}, b"missing")
        status, headers, body = routes(self.server.server_port).get(self.path, default)
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_port
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def fetcher():
    value = SafeHttpFetcher(
        resolve=fixture_resolve,
        address_allowed=lambda address: address in (PUBLIC, CLOSED),
        ports=None,
        max_bytes=1000,
        max_redirects=3,
    )
    yield value
    value.close()


def outcome(fetcher, url):
    try:
        page = fetcher.fetch(url)
    except FetchFailure as failure:
        return (failure.code, failure.retryable)
    return (page.media_type, page.content)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/page", ("text/html", b"<p>Hello</p>")),
        ("/zipped", ("text/plain", b"compressed body")),
        ("/to-internal", ("blocked_destination", False)),
        ("/to-ftp", ("blocked_destination", False)),
        ("/loop", ("too_many_redirects", False)),
        ("/large", ("page_too_large", False)),
        ("/image", ("unsupported_content", False)),
        ("/error", ("http_error", True)),
        ("/missing", ("not_found", False)),
    ],
)
def test_public_pages_succeed_and_everything_else_fails_safely(server, fetcher, path, expected):
    assert outcome(fetcher, f"http://public.test:{server}{path}") == expected


@pytest.mark.parametrize("host", ["internal.test", "mixed.test"])
def test_hosts_resolving_to_any_non_public_address_are_never_contacted(server, fetcher, host):
    Handler.hosts.clear()
    assert outcome(fetcher, f"http://{host}:{server}/page") == ("blocked_destination", False)
    assert Handler.hosts == []


def test_unknown_hosts_fail_as_dns_failures_not_as_blocked_destinations(server, fetcher):
    assert outcome(fetcher, f"http://unknown.test:{server}/page") == ("dns_failed", False)


@pytest.mark.parametrize(
    ("errno", "retryable"), [(socket.EAI_AGAIN, True), (socket.EAI_NONAME, False)]
)
def test_only_temporary_resolver_failures_are_retryable(monkeypatch, errno, retryable):
    def failing(*args, **kwargs):
        raise socket.gaierror(errno, "resolver says no")

    monkeypatch.setattr(socket, "getaddrinfo", failing)
    with pytest.raises(UnresolvedHost) as raised:
        system_resolve("docs.example.org", 443)
    assert raised.value.retryable is retryable


def test_a_dual_stack_host_falls_back_to_its_next_validated_address(server, fetcher):
    assert outcome(fetcher, f"http://dual.test:{server}/page") == ("text/html", b"<p>Hello</p>")


def test_non_ascii_urls_fail_safely_instead_of_crashing(server, fetcher):
    assert outcome(fetcher, f"http://public.test:{server}/r\u00e9sum\u00e9") == (
        "invalid_url",
        False,
    )


def test_requests_carry_exactly_one_host_header(server, fetcher):
    Handler.hosts.clear()
    fetcher.fetch(f"http://public.test:{server}/page")
    assert Handler.hosts == [[f"public.test:{server}"]]


def test_default_fetcher_refuses_loopback_and_non_web_ports(server):
    default = SafeHttpFetcher()
    try:
        results = [
            outcome(default, url)
            for url in ("http://127.0.0.1/", f"http://localhost:{server}/page")
        ]
    finally:
        default.close()
    assert results == [("blocked_destination", False)] * 2


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("8.8.8.8", True),
        ("2606:4700::1111", True),
        ("127.0.0.1", False),
        ("10.1.2.3", False),
        ("172.16.0.1", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),
        ("100.64.0.1", False),
        ("0.0.0.0", False),
        ("224.0.0.1", False),
        ("::1", False),
        ("fe80::1", False),
        ("fd00::1", False),
        ("::ffff:127.0.0.1", False),
        ("::ffff:8.8.8.8", True),
        ("64:ff9b::808:808", True),
        ("64:ff9b::a00:1", False),
        ("64:ff9b::a9fe:a9fe", False),
        ("::7f00:1", False),
        ("fec0::1", False),
        ("2002:7f00:1::1", False),
        ("not-an-ip", False),
    ],
)
def test_only_globally_routable_unicast_addresses_are_public(address, public):
    assert is_public_address(address) is public


def dribbling_server(head: bytes) -> tuple[socket.socket, int]:
    """Accepts one request, then sends `head` one byte at a time forever."""
    listener = socket.create_server(("127.0.0.1", 0))

    def serve():
        connection, _ = listener.accept()
        connection.recv(65536)
        try:
            for index in range(10_000):
                connection.sendall(head[index % len(head) : index % len(head) + 1])
                time.sleep(0.05)
        except OSError:
            pass
        finally:
            connection.close()

    threading.Thread(target=serve, daemon=True).start()
    return listener, listener.getsockname()[1]


@pytest.mark.parametrize(
    "head",
    [
        b"HTTP/1.1 200 OK\r\nX-Padding: " + b"a" * 5000,
        b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 4000\r\n\r\n"
        + b"b" * 3000,
    ],
    ids=["slow-headers", "slow-body"],
)
def test_a_server_dribbling_bytes_cannot_hold_a_fetch_past_its_wall_clock_budget(head):
    listener, port = dribbling_server(head)
    fetcher = SafeHttpFetcher(
        timeout_seconds=0.5, resolve=fixture_resolve, address_allowed=lambda a: True, ports=None
    )
    started = time.monotonic()
    try:
        result = outcome(fetcher, f"http://public.test:{port}/slow")
    finally:
        fetcher.close()
        listener.close()
    assert (result, time.monotonic() - started < 3) == (("fetch_timeout", True), True)
