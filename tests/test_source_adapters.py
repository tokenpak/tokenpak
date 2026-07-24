"""Unit tests for Source Adapters (Phase 3.3)."""

import pytest

pytest.importorskip(
    "tokenpak.connectors.notion_adapter", reason="module not available in current build"
)
import os
import subprocess
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from tokenpak.connectors.base_source import Provenance, SourceFetchError
from tokenpak.connectors.git_adapter import GitAdapter
from tokenpak.connectors.notion_adapter import NotionAdapter
from tokenpak.connectors.url_adapter import URLAdapter, _extract_title, _strip_html

# ---------------------------------------------------------------------------
# Provenance dataclass
# ---------------------------------------------------------------------------


class TestProvenance:
    def test_fields_populated(self):
        prov = Provenance(
            source_type="url",
            source_id="https://example.com",
            source_version="etag-abc",
            fetched_at="2026-02-25T00:00:00+00:00",
            title="Example Domain",
        )
        assert prov.source_type == "url"
        assert prov.source_id == "https://example.com"
        assert prov.source_version == "etag-abc"
        assert prov.title == "Example Domain"

    def test_title_defaults_empty(self):
        prov = Provenance("git", "src/auth.py", "abc123", "2026-02-25T00:00:00+00:00")
        assert prov.title == ""

    def test_all_source_types_accepted(self):
        for st in ("filesystem", "url", "notion", "git", "confluence", "sql", "s3"):
            prov = Provenance(st, "id", "ver", "ts")
            assert prov.source_type == st


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------


class TestHTMLHelpers:
    def test_strip_html_removes_tags(self):
        result = _strip_html("<p>Hello <b>world</b></p>")
        assert "<" not in result
        assert "Hello" in result
        assert "world" in result

    def test_strip_html_removes_scripts(self):
        result = _strip_html("<script>alert('xss')</script><p>safe</p>")
        assert "alert" not in result
        assert "safe" in result

    def test_strip_html_decodes_entities(self):
        result = _strip_html("&amp; &lt;tag&gt; &nbsp;")
        assert "&amp;" not in result
        assert "&" in result

    def test_extract_title(self):
        html = "<html><head><title>My Page Title</title></head><body></body></html>"
        assert _extract_title(html) == "My Page Title"

    def test_extract_title_missing(self):
        assert _extract_title("<html><body>no title</body></html>") == ""


# ---------------------------------------------------------------------------
# URL adapter
# ---------------------------------------------------------------------------


def _mock_response(body: bytes, headers: dict = None, status: int = 200):
    """Build a mock urllib response."""
    mock = MagicMock()
    mock.read.return_value = body
    mock.headers = MagicMock()
    mock.headers.get = lambda key, default="": (headers or {}).get(key, default)
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    return mock


class TestURLAdapter:
    def setup_method(self):
        self.adapter = URLAdapter()

    @patch("tokenpak.connectors.url_adapter._check_robots", return_value=True)
    @patch("urllib.request.urlopen")
    def test_ingest_returns_content_and_provenance(self, mock_urlopen, _robots):
        html = b"<html><head><title>Test Page</title></head><body><p>Hello world</p></body></html>"
        mock_urlopen.return_value = _mock_response(
            html, {"ETag": '"abc123"', "Content-Type": "text/html; charset=utf-8"}
        )
        content, prov = self.adapter.ingest("https://example.com")
        assert "Hello world" in content
        assert prov.source_type == "url"
        assert prov.source_id == "https://example.com"
        assert prov.source_version == "abc123"
        assert prov.title == "Test Page"

    @patch("tokenpak.connectors.url_adapter._check_robots", return_value=True)
    @patch("urllib.request.urlopen")
    def test_version_is_hash_when_no_etag(self, mock_urlopen, _robots):
        html = b"<html><body>no etag</body></html>"
        mock_urlopen.return_value = _mock_response(html, {"Content-Type": "text/html"})
        _, prov = self.adapter.ingest("https://example.com/no-etag")
        assert len(prov.source_version) == 64  # sha256 hex

    @patch("tokenpak.connectors.url_adapter._check_robots", return_value=False)
    def test_robots_disallowed_raises(self, _robots):
        with pytest.raises(SourceFetchError, match="robots.txt"):
            self.adapter.ingest("https://example.com/blocked")

    @patch("tokenpak.connectors.url_adapter._check_robots", return_value=True)
    @patch("urllib.request.urlopen", side_effect=Exception("connection refused"))
    def test_network_error_raises(self, _urlopen, _robots):
        with pytest.raises(SourceFetchError):
            self.adapter.ingest("https://unreachable.invalid")

    @patch("tokenpak.connectors.url_adapter._check_robots", return_value=True)
    @patch("urllib.request.urlopen")
    def test_has_changed_true_on_new_etag(self, mock_urlopen, _robots):
        mock_urlopen.return_value = _mock_response(b"", {"ETag": '"new-etag"'})
        assert self.adapter.has_changed("https://example.com", "old-etag") is True

    @patch("tokenpak.connectors.url_adapter._check_robots", return_value=True)
    @patch("urllib.request.urlopen")
    def test_has_changed_false_on_same_etag(self, mock_urlopen, _robots):
        mock_urlopen.return_value = _mock_response(b"", {"ETag": '"same-etag"'})
        assert self.adapter.has_changed("https://example.com", "same-etag") is False

    def test_source_type(self):
        assert self.adapter.source_type == "url"


# ---------------------------------------------------------------------------
# Notion adapter
# ---------------------------------------------------------------------------

_NOTION_PAGE = {
    "id": "abc123",
    "last_edited_time": "2026-02-25T10:00:00.000Z",
    "properties": {
        "title": {
            "type": "title",
            "title": [{"plain_text": "My Page"}],
        }
    },
}

_NOTION_BLOCKS = {
    "results": [
        {"type": "heading_1", "heading_1": {"rich_text": [{"plain_text": "Introduction"}]}},
        {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "This is a paragraph."}]}},
        {
            "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"plain_text": "Item one"}]},
        },
        {
            "type": "code",
            "code": {"language": "python", "rich_text": [{"plain_text": "def foo(): pass"}]},
        },
    ],
    "has_more": False,
}


class TestNotionAdapter:
    def setup_method(self):
        self.adapter = NotionAdapter(api_token="test-token")

    @patch("tokenpak.connectors.notion_adapter._get")
    def test_ingest_returns_content_and_provenance(self, mock_get):
        mock_get.side_effect = [_NOTION_PAGE, _NOTION_BLOCKS]
        content, prov = self.adapter.ingest("abc123")
        assert "Introduction" in content
        assert "This is a paragraph." in content
        assert "Item one" in content
        assert "def foo(): pass" in content
        assert prov.source_type == "notion"
        assert prov.title == "My Page"
        assert prov.source_version == "2026-02-25T10:00:00.000Z"

    @patch("tokenpak.connectors.notion_adapter._get")
    def test_has_changed_true_on_new_time(self, mock_get):
        mock_get.return_value = {**_NOTION_PAGE, "last_edited_time": "2026-02-26T00:00:00.000Z"}
        assert self.adapter.has_changed("abc123", "2026-02-25T10:00:00.000Z") is True

    @patch("tokenpak.connectors.notion_adapter._get")
    def test_has_changed_false_on_same_time(self, mock_get):
        mock_get.return_value = _NOTION_PAGE
        assert self.adapter.has_changed("abc123", "2026-02-25T10:00:00.000Z") is False

    def test_missing_token_raises(self):
        adapter = NotionAdapter(api_token=None)
        os.environ.pop("NOTION_API_TOKEN", None)
        with pytest.raises(SourceFetchError, match="token"):
            adapter.ingest("abc123")

    def test_source_type(self):
        assert self.adapter.source_type == "notion"


# ---------------------------------------------------------------------------
# Git adapter
# ---------------------------------------------------------------------------


def _init_git_repo(tmpdir: str, files: dict) -> str:
    """Create a minimal git repo with given files and return its path."""
    subprocess.run(["git", "init", tmpdir], capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], cwd=tmpdir, capture_output=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmpdir, capture_output=True)
    for path, content in files.items():
        full = os.path.join(tmpdir, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
    subprocess.run(["git", "add", "-A"], cwd=tmpdir, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmpdir,
        capture_output=True,
        check=True,
    )
    return tmpdir


class TestGitAdapter:
    def setup_method(self):
        self.adapter = GitAdapter()
        self.tmpdir = tempfile.mkdtemp()
        _init_git_repo(self.tmpdir, {"src/auth.py": "def login(): pass\n"})

    def test_ingest_reads_file_at_head(self):
        content, prov = self.adapter.ingest("src/auth.py", repo_path=self.tmpdir)
        assert "def login(): pass" in content
        assert prov.source_type == "git"
        assert prov.source_id == "src/auth.py"
        assert len(prov.source_version) == 40  # Full SHA

    def test_ingest_provenance_title(self):
        _, prov = self.adapter.ingest("src/auth.py", repo_path=self.tmpdir)
        assert "auth.py" in prov.title
        assert "@" in prov.title  # short sha included

    def test_has_changed_false_on_same_commit(self):
        _, prov = self.adapter.ingest("src/auth.py", repo_path=self.tmpdir)
        assert (
            self.adapter.has_changed("src/auth.py", prov.source_version, repo_path=self.tmpdir)
            is False
        )

    def test_has_changed_true_after_commit(self):
        _, prov_old = self.adapter.ingest("src/auth.py", repo_path=self.tmpdir)
        old_sha = prov_old.source_version

        # Make a new commit modifying auth.py
        with open(os.path.join(self.tmpdir, "src/auth.py"), "w") as f:
            f.write("def login(): return True\n")
        subprocess.run(["git", "add", "-A"], cwd=self.tmpdir, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "update"],
            cwd=self.tmpdir,
            capture_output=True,
        )

        assert self.adapter.has_changed("src/auth.py", old_sha, repo_path=self.tmpdir) is True

    def test_missing_repo_path_raises(self):
        with pytest.raises(SourceFetchError, match="repo_path"):
            self.adapter.ingest("src/auth.py")

    def test_missing_file_raises(self):
        with pytest.raises(SourceFetchError):
            self.adapter.ingest("nonexistent.py", repo_path=self.tmpdir)

    def test_source_type(self):
        assert self.adapter.source_type == "git"


# ---------------------------------------------------------------------------
# Wire format provenance block
# ---------------------------------------------------------------------------


class TestWireProvenance:
    def test_provenance_in_pack_output(self):
        from tokenpak.wire import pack

        prov = Provenance(
            source_type="url",
            source_id="https://example.com/docs",
            source_version="etag-xyz",
            fetched_at="2026-02-25T00:00:00+00:00",
            title="Docs",
        )
        blocks = [
            {
                "ref": "docs",
                "type": "text",
                "quality": 0.9,
                "tokens": 100,
                "content": "Important documentation.",
                "provenance": prov,
            }
        ]
        output = pack(blocks, budget=1000)
        assert "[SOURCE: url:https://example.com/docs]" in output
        assert "[VERSION:" in output

    def test_no_provenance_no_source_tag(self):
        from tokenpak.wire import pack

        blocks = [
            {
                "ref": "local",
                "type": "code",
                "quality": 1.0,
                "tokens": 50,
                "content": "def foo(): pass",
            }
        ]
        output = pack(blocks, budget=1000)
        assert "[SOURCE:" not in output

    def test_version_truncated_to_16_chars(self):
        from tokenpak.wire import pack

        prov = Provenance("git", "src/main.py", "a" * 40, "2026-02-25T00:00:00+00:00")
        blocks = [
            {
                "ref": "x",
                "type": "code",
                "quality": 1.0,
                "tokens": 10,
                "content": "x=1",
                "provenance": prov,
            }
        ]
        output = pack(blocks, budget=1000)
        assert f"[VERSION: {'a' * 16}]" in output
