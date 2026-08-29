"""
Fetching a label from a link the family pasted or dragged in, as an
alternative to uploading a file.

This is the one place the app makes an outbound request instead of only
serving inbound ones, so it gets treated as hostile-ish in both directions:
the response has to look like a label, and the destination has to not be
somewhere on the LAN the app has no business reaching (the Pi itself, a
router's admin page, another device). That check runs before *and* after
following a redirect, since a public URL can still 302 to a private one.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlparse

USER_AGENT = "labelserver/1.0 (+https://github.com/mmoniz/printer-server)"
CONNECT_TIMEOUT = 10
MAX_REDIRECTS = 5

# Keyed by the response's Content-Type so a generic filename in the URL
# (or none at all) still gets a suffix normalize_upload can key off of.
CONTENT_TYPE_SUFFIXES = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/webp": ".webp",
}


class FetchError(Exception):
    """A link could not be turned into something we can print."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Hands 3xx responses back instead of following them automatically, so
    the target of each hop can be validated before we ever connect to it."""

    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _check_destination(hostname: str) -> None:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise FetchError(f"Could not resolve {hostname}.") from exc

    for *_, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            raise FetchError(
                "That link points at a private or internal address, "
                "which isn't allowed.")


def fetch_url(url: str, max_bytes: int) -> tuple[bytes, str]:
    """Fetch url and return (data, filename). Raises FetchError."""
    current = url

    for _ in range(MAX_REDIRECTS + 1):
        parsed = urlparse(current)
        if parsed.scheme not in ("http", "https"):
            raise FetchError("Only http:// and https:// links are supported.")
        if not parsed.hostname:
            raise FetchError("That doesn't look like a valid link.")

        _check_destination(parsed.hostname)

        request = urllib.request.Request(
            current, headers={"User-Agent": USER_AGENT})
        try:
            response = _opener.open(request, timeout=CONNECT_TIMEOUT)
        except urllib.error.HTTPError as exc:
            # _NoRedirect stops the redirect from being followed automatically
            # (which would skip _check_destination on every hop but the
            # first), but the fallthrough this produces is an HTTPError
            # carrying the redirect's own status and Location, not a real
            # error -- so we do the following ourselves, from scratch, with
            # the new host validated before we ever connect to it.
            if exc.code in (301, 302, 303, 307, 308):
                location = exc.headers.get("Location")
                if not location:
                    raise FetchError("That link redirected with nowhere to go.") from exc
                current = urljoin(current, location)
                continue
            raise FetchError(f"The link returned an error ({exc.code}).") from exc
        except urllib.error.URLError as exc:
            raise FetchError(f"Could not reach that link: {exc.reason}") from exc
        except socket.timeout as exc:
            raise FetchError("That link took too long to respond.") from exc

        with response:
            content_type = response.headers.get_content_type()
            suffix = CONTENT_TYPE_SUFFIXES.get(content_type)
            if suffix is None:
                raise FetchError(
                    f"That link isn't a PDF or image "
                    f"({content_type or 'unknown type'}).")

            data = response.read(max_bytes + 1)

        if len(data) > max_bytes:
            raise FetchError(
                f"That file is too big (limit {max_bytes // (1024 * 1024)} MB).")

        filename = os.path.basename(parsed.path) or "label"
        if not filename.lower().endswith(suffix):
            filename += suffix
        return data, filename

    raise FetchError("That link redirected too many times.")
