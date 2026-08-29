"""
Tests for labelserver/urlfetch.py.

This is the one module that makes an outbound request, so it gets tested
against a real (local, in-process) HTTP server rather than mocked -- the
redirect handling and the private-address check both depend on urllib's
actual behavior, which is exactly what would be easy to get subtly wrong.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from labelserver import urlfetch as urlfetch_module
from labelserver.urlfetch import FetchError, fetch_url

PDF_BYTES = b"%PDF-1.4 not a real pdf, just needs a content type"


class Handler(BaseHTTPRequestHandler):
    routes: dict[str, tuple[int, str | None, bytes, dict]] = {}

    def do_GET(self):
        status, content_type, body, extra_headers = self.routes.get(
            self.path, (404, "text/plain", b"not found", {}))
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        for key, value in extra_headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server(monkeypatch):
    """A real local server for exercising fetch/redirect/content-type
    handling -- which necessarily lives on loopback, so the private-address
    check (tested on its own below) is disabled here rather than defeated by
    every other test having to work around it."""
    monkeypatch.setattr(urlfetch_module, "_check_destination", lambda hostname: None)

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    thread.join()


def base_url(server) -> str:
    return f"http://127.0.0.1:{server.server_port}"


def test_fetches_a_pdf(server):
    Handler.routes = {"/label.pdf": (200, "application/pdf", PDF_BYTES, {})}
    data, filename = fetch_url(f"{base_url(server)}/label.pdf", max_bytes=1_000_000)
    assert data == PDF_BYTES
    assert filename == "label.pdf"


def test_infers_a_suffix_from_content_type_when_the_url_has_none(server):
    Handler.routes = {"/download": (200, "image/png", b"\x89PNG", {})}
    data, filename = fetch_url(f"{base_url(server)}/download", max_bytes=1_000_000)
    assert filename == "download.png"


def test_follows_a_redirect(server):
    Handler.routes = {
        "/old": (302, None, b"", {"Location": "/new.pdf"}),
        "/new.pdf": (200, "application/pdf", PDF_BYTES, {}),
    }
    data, filename = fetch_url(f"{base_url(server)}/old", max_bytes=1_000_000)
    assert data == PDF_BYTES
    assert filename == "new.pdf"


def test_too_many_redirects_is_rejected(server):
    for i in range(10):
        Handler.routes[f"/hop{i}"] = (302, None, b"", {"Location": f"/hop{i + 1}"})
    with pytest.raises(FetchError, match="too many times"):
        fetch_url(f"{base_url(server)}/hop0", max_bytes=1_000_000)


def test_oversized_response_is_rejected(server):
    Handler.routes = {"/big.pdf": (200, "application/pdf", b"x" * 1000, {})}
    with pytest.raises(FetchError, match="too big"):
        fetch_url(f"{base_url(server)}/big.pdf", max_bytes=100)


def test_unsupported_content_type_is_rejected(server):
    Handler.routes = {"/page": (200, "application/json", b"{}", {})}
    with pytest.raises(FetchError, match="isn't a PDF or image"):
        fetch_url(f"{base_url(server)}/page", max_bytes=1_000_000)


def test_html_response_suggests_dragging_the_image_instead(server):
    """The common real-world cause: a link that needs an active login
    session (an Amazon return/shipping label, for one) hands back a
    sign-in or error page instead of the file. We have no session to
    offer, so the message should point at what actually works."""
    Handler.routes = {"/page": (200, "text/html", b"<html>sign in</html>", {})}
    with pytest.raises(FetchError, match="drag or paste the label image"):
        fetch_url(f"{base_url(server)}/page", max_bytes=1_000_000)


def test_http_error_is_reported(server):
    Handler.routes = {"/missing.pdf": (404, "text/plain", b"nope", {})}
    with pytest.raises(FetchError, match="error \\(404\\)"):
        fetch_url(f"{base_url(server)}/missing.pdf", max_bytes=1_000_000)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/label.pdf",
    "http://localhost/label.pdf",
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata endpoint
    "http://[::1]/label.pdf",
])
def test_private_and_loopback_destinations_are_rejected(url):
    with pytest.raises(FetchError, match="private or internal"):
        fetch_url(url, max_bytes=1_000_000)


def test_a_redirect_target_is_revalidated_independently(monkeypatch, server):
    """A public first hop must not exempt a private second hop.

    The real private-address check would reject our loopback test server on
    the *first* hop too, which would pass for the wrong reason. Swap in a
    fake check instead, so this actually proves each hop's host is looked up
    on its own -- not just the URL the fetch started with.
    """
    checked = []

    def fake_check(hostname):
        checked.append(hostname)
        if hostname == "internal.example":
            raise FetchError("private or internal (faked for this test)")

    monkeypatch.setattr(urlfetch_module, "_check_destination", fake_check)

    Handler.routes = {
        "/redirect": (302, None, b"", {"Location": "http://internal.example/secret"}),
    }
    with pytest.raises(FetchError, match="private or internal"):
        fetch_url(f"{base_url(server)}/redirect", max_bytes=1_000_000)

    assert checked == ["127.0.0.1", "internal.example"]


@pytest.mark.parametrize("url", [
    "ftp://example.com/label.pdf",
    "file:///etc/passwd",
    "not a url",
])
def test_disallowed_schemes_are_rejected(url):
    with pytest.raises(FetchError):
        fetch_url(url, max_bytes=1_000_000)
