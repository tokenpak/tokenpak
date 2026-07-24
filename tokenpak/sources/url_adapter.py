"""URL SourceAdapter for TokenPak.

Fetches web pages, strips HTML to clean text, detects changes via ETag.
Respects robots.txt before fetching. No third-party deps (stdlib only).
"""

from __future__ import annotations

import html
import ipaddress
import re
import socket
import urllib.parse
import urllib.request
import urllib.robotparser
from http.client import HTTPMessage
from types import TracebackType
from typing import IO, Protocol, cast

from .base_source import Provenance, SourceAdapter, SourceFetchError

# Timeout for HTTP requests (seconds)
_HTTP_TIMEOUT = 10

# Strip these common boilerplate tags (with their content)
_STRIP_TAGS_WITH_CONTENT = re.compile(
    r"<(script|style|noscript|header|footer|nav|aside)[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
# Remove remaining HTML tags
_STRIP_TAGS = re.compile(r"<[^>]+>")
# Collapse whitespace
_COLLAPSE_WS = re.compile(r"\s{2,}")
_METADATA_IPS = {ipaddress.ip_address("169.254.169.254")}


class _URLResponse(Protocol):
    headers: HTTPMessage

    def read(self) -> bytes: ...

    def getcode(self) -> int: ...

    def __enter__(self) -> _URLResponse: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


def _strip_html(raw_html: str) -> str:
    """Convert HTML to readable plain text (stdlib only)."""
    # Decode HTML entities first
    text = html.unescape(raw_html)
    # Strip boilerplate sections
    text = _STRIP_TAGS_WITH_CONTENT.sub(" ", text)
    # Remove remaining tags
    text = _STRIP_TAGS.sub(" ", text)
    # Collapse whitespace
    text = _COLLAPSE_WS.sub(" ", text)
    return text.strip()


def _extract_title(raw_html: str) -> str:
    """Extract <title> tag content."""
    m = re.search(r"<title[^>]*>(.*?)</title>", raw_html, re.IGNORECASE | re.DOTALL)
    if m:
        return html.unescape(m.group(1).strip())
    return ""


def _check_robots(url: str) -> bool:
    """
    Return True if the URL is allowed by robots.txt.
    Returns True on error (fail-open — don't block fetch on robots.txt issues).
    """
    try:
        parsed = urllib.parse.urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots_url)
        rp.read()
        return rp.can_fetch("TokenPak/1.0", url)
    except Exception:
        return True  # Fail-open


def _is_blocked_address(address: str) -> bool:
    """Return True for local/private addresses that must never be fetched."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return (
        ip in _METADATA_IPS
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _validate_url_safe(url: str) -> None:
    """Fail closed for schemes and address ranges that can SSRF local services."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise SourceFetchError(f"Unsupported URL scheme: {parsed.scheme or '<missing>'}")
    if not parsed.hostname:
        raise SourceFetchError("URL host is required")
    try:
        port = parsed.port
    except ValueError as exc:
        raise SourceFetchError("Invalid URL port") from exc

    host = parsed.hostname
    if host.lower() == "localhost" or _is_blocked_address(host):
        raise SourceFetchError(f"Blocked local or private URL host: {host}")

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return
    for info in infos:
        resolved = str(info[4][0])
        if _is_blocked_address(resolved):
            raise SourceFetchError(f"Blocked local or private URL host: {host}")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Validate redirect targets before urllib follows them."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        _validate_url_safe(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _urlopen_checked(req: urllib.request.Request, timeout: int) -> _URLResponse:
    opener = urllib.request.build_opener(_SafeRedirectHandler)
    return cast(_URLResponse, opener.open(req, timeout=timeout))


class URLAdapter(SourceAdapter):
    """Fetch and index web pages by URL."""

    source_type = "url"

    def ingest(self, source_id: str, **kwargs: object) -> tuple[str, Provenance]:
        """
        Fetch a URL and return clean text + provenance.

        Args:
            source_id: Full URL (http/https).
            **kwargs:  Unused.

        Returns:
            (content, Provenance)
        """
        url = source_id
        _validate_url_safe(url)

        if not _check_robots(url):
            raise SourceFetchError(f"robots.txt disallows fetching: {url}")

        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "TokenPak/1.0 (context indexer)"},
            )
            with _urlopen_checked(req, timeout=_HTTP_TIMEOUT) as resp:
                raw_bytes = resp.read()
                etag = resp.headers.get("ETag") or ""
                content_type = resp.headers.get("Content-Type") or ""
        except Exception as exc:
            raise SourceFetchError(f"Failed to fetch {url}: {exc}") from exc

        # Decode content
        encoding = "utf-8"
        if "charset=" in content_type:
            encoding = content_type.split("charset=")[-1].split(";")[0].strip()
        try:
            raw_text = raw_bytes.decode(encoding, errors="replace")
        except (LookupError, UnicodeDecodeError):
            raw_text = raw_bytes.decode("utf-8", errors="replace")

        # Convert HTML → text
        if "html" in content_type.lower():
            title = _extract_title(raw_text)
            content = _strip_html(raw_text)
        else:
            title = url
            content = raw_text

        # source_version: ETag if available, else SHA-256 of content
        version = etag.strip('"') if etag else self._sha256(content)

        provenance = Provenance(
            source_type=self.source_type,
            source_id=url,
            source_version=version,
            fetched_at=self._now(),
            title=title or url,
        )
        return content, provenance

    def has_changed(self, source_id: str, cached_version: str, **kwargs: object) -> bool:
        """
        Check whether the page has changed via a HEAD request + ETag comparison.
        Falls back to full fetch + hash if HEAD returns no ETag.
        """
        url = source_id
        try:
            _validate_url_safe(url)
        except SourceFetchError:
            return False
        try:
            req = urllib.request.Request(
                url,
                method="HEAD",
                headers={"User-Agent": "TokenPak/1.0"},
            )
            with _urlopen_checked(req, timeout=_HTTP_TIMEOUT) as resp:
                etag = (resp.headers.get("ETag") or "").strip('"')
            if etag:
                return etag != cached_version
        except Exception:
            pass

        # Fallback: re-fetch and compare hash
        try:
            content, prov = self.ingest(url)
            return prov.source_version != cached_version
        except SourceFetchError:
            return False  # Can't determine → assume unchanged
