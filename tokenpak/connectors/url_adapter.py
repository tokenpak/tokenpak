"""URL SourceAdapter for TokenPak.

Fetches web pages, strips HTML to clean text, detects changes via ETag.
Respects robots.txt before fetching. No third-party deps (stdlib only).
"""

import html
import re
import urllib.parse
import urllib.request
import urllib.robotparser
from typing import Tuple

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


class URLAdapter(SourceAdapter):
    """Fetch and index web pages by URL."""

    source_type = "url"

    def ingest(self, source_id: str, **kwargs) -> Tuple[str, Provenance]:
        """
        Fetch a URL and return clean text + provenance.

        Args:
            source_id: Full URL (http/https).
            **kwargs:  Unused.

        Returns:
            (content, Provenance)
        """
        url = source_id

        if not _check_robots(url):
            raise SourceFetchError(f"robots.txt disallows fetching: {url}")

        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "TokenPak/1.0 (context indexer)"},
            )
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
                raw_bytes = resp.read()
                etag = resp.headers.get("ETag", "")
                content_type = resp.headers.get("Content-Type", "")
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

    def has_changed(self, source_id: str, cached_version: str, **kwargs) -> bool:
        """
        Check whether the page has changed via a HEAD request + ETag comparison.
        Falls back to full fetch + hash if HEAD returns no ETag.
        """
        url = source_id
        try:
            req = urllib.request.Request(
                url,
                method="HEAD",
                headers={"User-Agent": "TokenPak/1.0"},
            )
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
                etag = resp.headers.get("ETag", "").strip('"')
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
