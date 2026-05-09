# SPDX-License-Identifier: Apache-2.0
"""Unit tests for connectors.url_adapter — URLAdapter and helpers."""

from http.client import HTTPMessage
from unittest.mock import MagicMock, patch

import pytest

from tokenpak.sources.base_source import SourceFetchError
from tokenpak.sources.url_adapter import (
    URLAdapter,
    _check_robots,
    _extract_title,
    _strip_html,
)

# ---------------------------------------------------------------------------
# _strip_html
# ---------------------------------------------------------------------------


class TestStripHtml:
    def test_removes_basic_tags(self):
        result = _strip_html("<p>Hello <b>World</b></p>")
        assert "Hello" in result
        assert "World" in result
        assert "<" not in result

    def test_removes_script_content(self):
        result = _strip_html("<p>text</p><script>alert('xss')</script>")
        assert "alert" not in result
        assert "text" in result

    def test_removes_style_content(self):
        result = _strip_html("<style>body { color: red; }</style><p>content</p>")
        assert "color" not in result
        assert "content" in result

    def test_decodes_html_entities(self):
        result = _strip_html("<p>5 &gt; 3 &amp; 2 &lt; 4</p>")
        assert ">" in result
        assert "&" in result
        assert "<" not in result

    def test_collapses_whitespace(self):
        result = _strip_html("<p>  too   many   spaces  </p>")
        assert "  " not in result.strip()

    def test_empty_html(self):
        result = _strip_html("")
        assert result == ""

    def test_plain_text_unchanged(self):
        result = _strip_html("just plain text")
        assert "just plain text" in result

    def test_removes_nav_header_footer(self):
        result = _strip_html(
            "<nav>nav content</nav><main>main content</main><footer>footer</footer>"
        )
        assert "nav content" not in result
        assert "footer" not in result
        assert "main content" in result


# ---------------------------------------------------------------------------
# _extract_title
# ---------------------------------------------------------------------------


class TestExtractTitle:
    def test_extracts_title(self):
        html = "<html><head><title>My Page Title</title></head><body></body></html>"
        assert _extract_title(html) == "My Page Title"

    def test_decodes_entities_in_title(self):
        html = "<title>A &amp; B &lt;Page&gt;</title>"
        title = _extract_title(html)
        assert "A & B" in title

    def test_no_title_returns_empty(self):
        html = "<html><body><p>No title tag</p></body></html>"
        assert _extract_title(html) == ""

    def test_multiline_title(self):
        html = "<title>\n  Spaced Title\n</title>"
        assert _extract_title(html).strip() == "Spaced Title"

    def test_empty_title_tag(self):
        html = "<title></title>"
        assert _extract_title(html) == ""


# ---------------------------------------------------------------------------
# _check_robots
# ---------------------------------------------------------------------------


class TestCheckRobots:
    def test_returns_true_on_robots_error(self):
        """Fail-open: errors in robots.txt checking allow the fetch."""
        with patch("urllib.robotparser.RobotFileParser.read", side_effect=Exception("timeout")):
            result = _check_robots("http://example.com/page")
        assert result is True

    def test_returns_true_when_allowed(self):
        with patch("urllib.robotparser.RobotFileParser") as mock_rp_class:
            mock_rp = MagicMock()
            mock_rp.can_fetch.return_value = True
            mock_rp_class.return_value = mock_rp
            result = _check_robots("http://example.com/page")
        assert result is True

    def test_returns_false_when_disallowed(self):
        with patch("urllib.robotparser.RobotFileParser") as mock_rp_class:
            mock_rp = MagicMock()
            mock_rp.can_fetch.return_value = False
            mock_rp_class.return_value = mock_rp
            result = _check_robots("http://example.com/secret")
        assert result is False


# ---------------------------------------------------------------------------
# URLAdapter.ingest()
# ---------------------------------------------------------------------------


def _make_mock_response(body: bytes, etag: str = "", content_type: str = "text/html"):
    """Helper to build a mock urllib response context manager."""
    headers = HTTPMessage()
    if etag:
        headers["ETag"] = etag
    headers["Content-Type"] = content_type

    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.headers = headers
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class TestURLAdapterIngest:
    def _adapter(self):
        return URLAdapter()

    def test_ingest_html_returns_content_and_provenance(self):
        html = b"<html><head><title>Test Page</title></head><body><p>Hello</p></body></html>"
        mock_resp = _make_mock_response(html, etag='"abc123"')

        with patch("urllib.request.urlopen", return_value=mock_resp), \
             patch("tokenpak.sources.url_adapter._check_robots", return_value=True):
            content, prov = self._adapter().ingest("http://example.com/test")

        assert "Hello" in content
        assert prov.source_type == "url"
        assert prov.source_id == "http://example.com/test"
        assert prov.title == "Test Page"

    def test_ingest_uses_etag_as_version(self):
        html = b"<html><title>X</title><body>hi</body></html>"
        mock_resp = _make_mock_response(html, etag='"etag-value"')

        with patch("urllib.request.urlopen", return_value=mock_resp), \
             patch("tokenpak.sources.url_adapter._check_robots", return_value=True):
            _, prov = self._adapter().ingest("http://example.com/")

        assert prov.source_version == "etag-value"

    def test_ingest_falls_back_to_sha256_without_etag(self):
        html = b"<html><title>X</title><body>content</body></html>"
        mock_resp = _make_mock_response(html, etag="")  # No ETag

        with patch("urllib.request.urlopen", return_value=mock_resp), \
             patch("tokenpak.sources.url_adapter._check_robots", return_value=True):
            _, prov = self._adapter().ingest("http://example.com/page")

        # Version should be a 64-char hex SHA
        assert len(prov.source_version) == 64
        assert all(c in "0123456789abcdef" for c in prov.source_version)

    def test_ingest_raises_when_robots_disallows(self):
        with patch("tokenpak.sources.url_adapter._check_robots", return_value=False):
            with pytest.raises(SourceFetchError, match="robots.txt"):
                self._adapter().ingest("http://example.com/protected")

    def test_ingest_raises_on_network_error(self):
        with patch("urllib.request.urlopen", side_effect=Exception("connection refused")), \
             patch("tokenpak.sources.url_adapter._check_robots", return_value=True):
            with pytest.raises(SourceFetchError, match="Failed to fetch"):
                self._adapter().ingest("http://bad-host.invalid/page")

    def test_ingest_plain_text_content_type(self):
        plain = b"Just plain text content"
        mock_resp = _make_mock_response(plain, content_type="text/plain")

        with patch("urllib.request.urlopen", return_value=mock_resp), \
             patch("tokenpak.sources.url_adapter._check_robots", return_value=True):
            content, prov = self._adapter().ingest("http://example.com/file.txt")

        assert "Just plain text content" in content
        # For plain text, title falls back to the URL
        assert prov.title == "http://example.com/file.txt"

    def test_ingest_fetched_at_is_iso_timestamp(self):
        html = b"<html><title>T</title><body>x</body></html>"
        mock_resp = _make_mock_response(html)

        with patch("urllib.request.urlopen", return_value=mock_resp), \
             patch("tokenpak.sources.url_adapter._check_robots", return_value=True):
            _, prov = self._adapter().ingest("http://example.com/")

        assert "T" in prov.fetched_at

    def test_source_type_is_url(self):
        assert URLAdapter.source_type == "url" or URLAdapter().source_type == "url"


# ---------------------------------------------------------------------------
# URLAdapter.has_changed()
# ---------------------------------------------------------------------------


class TestURLAdapterHasChanged:
    def _adapter(self):
        return URLAdapter()

    def test_returns_false_when_etag_matches(self):
        head_resp = MagicMock()
        head_resp.headers = HTTPMessage()
        head_resp.headers["ETag"] = '"same-etag"'
        head_resp.__enter__ = lambda s: s
        head_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=head_resp):
            result = self._adapter().has_changed("http://example.com/", "same-etag")

        assert result is False

    def test_returns_true_when_etag_differs(self):
        head_resp = MagicMock()
        head_resp.headers = HTTPMessage()
        head_resp.headers["ETag"] = '"new-etag"'
        head_resp.__enter__ = lambda s: s
        head_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=head_resp):
            result = self._adapter().has_changed("http://example.com/", "old-etag")

        assert result is True

    def test_returns_false_on_fetch_error_during_fallback(self):
        """When HEAD fails and fallback ingest also fails, assume unchanged."""
        with patch("urllib.request.urlopen", side_effect=Exception("network error")):
            result = self._adapter().has_changed("http://bad.invalid/", "any-version")
        assert result is False
