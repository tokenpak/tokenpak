"""Unit tests for the connectors module (uncovered modules).

Covers:
- connectors/__init__.py  (get_connector, list_connectors)
- connectors/base_source.py  (Provenance, SourceAdapter, SourceFetchError)
- connectors/github.py  (GitHubConnector)
- connectors/google_drive.py  (GoogleDriveConnector)
- connectors/notion.py  (NotionConnector, _to_remote_file, _blocks_to_markdown)
- connectors/notion_adapter.py  (helpers, NotionAdapter — all network mocked)
"""

import hashlib
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from tokenpak.connectors import get_connector, list_connectors
from tokenpak.connectors.base import ConnectorConfig, RemoteFile
from tokenpak.connectors.base_source import (
    Provenance,
    SourceAdapter,
    SourceFetchError,
)
from tokenpak.connectors.github import GitHubConnector
from tokenpak.connectors.google_drive import GoogleDriveConnector
from tokenpak.connectors.notion import NotionConnector
from tokenpak.connectors.notion_adapter import (
    NotionAdapter,
    _block_to_text,
    _extract_rich_text,
    _notion_headers,
    _page_title,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_config(source_path="owner/repo", auth_token="tok123"):
    return ConnectorConfig(name="test", source_path=source_path, auth_token=auth_token)


# ===========================================================================
# connectors/__init__.py
# ===========================================================================


class TestConnectorsInit:
    def test_package_importable(self):
        import tokenpak.connectors  # noqa: F401

    def test_list_connectors_returns_list(self):
        result = list_connectors()
        assert isinstance(result, list)

    def test_list_connectors_contains_local(self):
        assert "local" in list_connectors()

    def test_list_connectors_contains_obsidian(self):
        assert "obsidian" in list_connectors()

    def test_get_connector_local_returns_instance(self):
        import tempfile, os

        with tempfile.TemporaryDirectory() as td:
            cfg = ConnectorConfig(name="local", source_path=td)
            connector = get_connector("local", cfg)
            assert connector is not None

    def test_get_connector_unknown_raises_value_error(self):
        cfg = ConnectorConfig(name="x", source_path="/tmp")
        with pytest.raises(ValueError, match="Unknown connector"):
            get_connector("nonexistent_xyz", cfg)


# ===========================================================================
# connectors/base_source.py
# ===========================================================================


class TestProvenance:
    def test_instantiation_required_fields(self):
        p = Provenance(
            source_type="url",
            source_id="https://example.com",
            source_version="abc123",
            fetched_at="2026-01-01T00:00:00+00:00",
        )
        assert p.source_type == "url"
        assert p.source_id == "https://example.com"
        assert p.source_version == "abc123"
        assert p.title == ""

    def test_optional_title_field(self):
        p = Provenance(
            source_type="fs",
            source_id="/a/b.md",
            source_version="v1",
            fetched_at="2026-01-01T00:00:00+00:00",
            title="My Doc",
        )
        assert p.title == "My Doc"


class TestSourceFetchError:
    def test_is_exception(self):
        err = SourceFetchError("boom")
        assert isinstance(err, Exception)

    def test_message_preserved(self):
        with pytest.raises(SourceFetchError, match="boom"):
            raise SourceFetchError("boom")


class TestSourceAdapterHelpers:
    """Test the shared static helpers on SourceAdapter via a minimal subclass."""

    class _ConcreteAdapter(SourceAdapter):
        source_type = "test"

        def ingest(self, source_id, **kwargs):
            return "", Provenance("test", source_id, "", self._now())

        def has_changed(self, source_id, cached_version, **kwargs):
            return False

    def test_sha256_known_value(self):
        expected = hashlib.sha256(b"hello").hexdigest()
        assert self._ConcreteAdapter._sha256("hello") == expected

    def test_sha256_empty_string(self):
        expected = hashlib.sha256(b"").hexdigest()
        assert self._ConcreteAdapter._sha256("") == expected

    def test_now_returns_iso_string(self):
        ts = self._ConcreteAdapter._now()
        # Should parse without error as an ISO datetime
        dt = datetime.fromisoformat(ts)
        assert dt.tzinfo is not None  # timezone-aware

    def test_subclass_instantiable(self):
        adapter = self._ConcreteAdapter()
        assert adapter.source_type == "test"


# ===========================================================================
# connectors/github.py
# ===========================================================================


class TestGitHubConnector:
    def _make(self, source_path="owner/repo", auth_token="ghp_token"):
        return GitHubConnector(make_config(source_path, auth_token))

    def test_name_and_tier(self):
        c = self._make()
        assert c.name == "github"
        assert c.tier == "pro"

    def test_connect_no_slash_returns_false(self):
        c = self._make(source_path="noslash")
        assert c.connect() is False

    def test_connect_no_auth_token_returns_false(self):
        c = self._make(auth_token=None)
        assert c.connect() is False

    def test_connect_valid_raises_not_implemented(self):
        c = self._make()
        with pytest.raises(NotImplementedError):
            c.connect()

    def test_list_files_raises_not_implemented(self):
        c = self._make()
        with pytest.raises(NotImplementedError):
            list(c.list_files())

    def test_get_content_raises_not_implemented(self):
        c = self._make()
        rf = RemoteFile(path="f.py", source_id="sha1", size_bytes=0, modified_at="")
        with pytest.raises(NotImplementedError):
            c.get_content(rf)

    def test_list_issues_raises_not_implemented(self):
        c = self._make()
        with pytest.raises(NotImplementedError):
            list(c.list_issues())

    def test_detect_language_python(self):
        assert GitHubConnector._detect_language(None, "script.py") == "python"  # type: ignore

    def test_detect_language_typescript(self):
        assert GitHubConnector._detect_language(None, "app.ts") == "typescript"  # type: ignore

    def test_detect_language_tsx(self):
        assert GitHubConnector._detect_language(None, "comp.tsx") == "typescript"  # type: ignore

    def test_detect_language_markdown(self):
        assert GitHubConnector._detect_language(None, "README.md") == "markdown"  # type: ignore

    def test_detect_language_yaml(self):
        assert GitHubConnector._detect_language(None, "config.yml") == "yaml"  # type: ignore

    def test_detect_language_unknown(self):
        assert GitHubConnector._detect_language(None, "file.xyz") == "unknown"  # type: ignore

    def test_slugify_basic(self):
        c = self._make()
        assert c._slugify("Hello World!") == "hello-world"

    def test_slugify_truncates_at_50(self):
        c = self._make()
        long_text = "a " * 40  # > 50 chars
        result = c._slugify(long_text)
        assert len(result) <= 50

    def test_slugify_special_chars(self):
        c = self._make()
        result = c._slugify("Fix: bug #123 (critical)")
        assert "#" not in result
        assert "(" not in result

    def test_connect_sets_owner_and_repo(self):
        c = self._make(source_path="myorg/myrepo", auth_token="token")
        try:
            c.connect()
        except NotImplementedError:
            pass
        assert c._owner == "myorg"
        assert c._repo == "myrepo"


# ===========================================================================
# connectors/google_drive.py
# ===========================================================================


class TestGoogleDriveConnector:
    def _make(self):
        return GoogleDriveConnector(make_config())

    def test_name_and_tier(self):
        c = self._make()
        assert c.name == "google_drive"
        assert c.tier == "pro"

    def test_export_formats_dict_present(self):
        assert "application/vnd.google-apps.document" in GoogleDriveConnector.EXPORT_FORMATS

    def test_connect_returns_false(self):
        # NotImplementedError is caught internally → returns False
        c = self._make()
        result = c.connect()
        assert result is False

    def test_list_files_not_connected_raises_runtime(self):
        c = self._make()
        with pytest.raises(RuntimeError, match="Not connected"):
            list(c.list_files())

    def test_list_files_with_since_not_connected_raises_runtime(self):
        c = self._make()
        with pytest.raises(RuntimeError, match="Not connected"):
            list(c.list_files(since="some-token"))

    def test_get_content_not_connected_raises_runtime(self):
        c = self._make()
        rf = RemoteFile(path="doc.txt", source_id="file_id", size_bytes=0, modified_at="")
        with pytest.raises(RuntimeError, match="Not connected"):
            c.get_content(rf)

    def test_to_remote_file_basic(self):
        c = self._make()
        drive_file = {
            "id": "abc123",
            "name": "report.docx",
            "mimeType": "application/vnd.google-apps.document",
            "size": "1024",
            "modifiedTime": "2026-01-01T00:00:00Z",
        }
        rf = c._to_remote_file(drive_file)
        assert rf.source_id == "abc123"
        assert rf.path == "report.docx"
        assert rf.size_bytes == 1024
        assert rf.file_type == "application/vnd.google-apps.document"

    def test_to_remote_file_missing_fields(self):
        c = self._make()
        rf = c._to_remote_file({})
        assert rf.path == "unknown"
        assert rf.source_id is None
        assert rf.size_bytes == 0


# ===========================================================================
# connectors/notion.py
# ===========================================================================


class TestNotionConnector:
    def _make(self, auth_token="notion_token"):
        return NotionConnector(make_config(auth_token=auth_token))

    def test_name_and_tier(self):
        c = self._make()
        assert c.name == "notion"
        assert c.tier == "pro"

    def test_notion_api_base(self):
        assert "notion.com" in NotionConnector.NOTION_API_BASE

    def test_connect_no_token_returns_false(self):
        c = self._make(auth_token=None)
        assert c.connect() is False

    def test_connect_with_token_raises_not_implemented(self):
        c = self._make()
        with pytest.raises(NotImplementedError):
            c.connect()

    def test_connect_sets_headers(self):
        c = self._make()
        try:
            c.connect()
        except NotImplementedError:
            pass
        assert c._headers is not None
        assert "Authorization" in c._headers
        assert "Notion-Version" in c._headers

    def test_list_files_raises_not_implemented(self):
        c = self._make()
        with pytest.raises(NotImplementedError):
            list(c.list_files())

    def test_get_content_raises_not_implemented(self):
        c = self._make()
        rf = RemoteFile(path="page.md", source_id="page_id", size_bytes=0, modified_at="")
        with pytest.raises(NotImplementedError):
            c.get_content(rf)

    def test_to_remote_file_basic(self):
        c = self._make()
        notion_obj = {
            "id": "page-123",
            "object": "page",
            "last_edited_time": "2026-01-01T00:00:00Z",
            "properties": {},
        }
        rf = c._to_remote_file(notion_obj)
        assert rf.source_id == "page-123"
        assert rf.file_type == "page"

    def test_to_remote_file_with_title_list_prop(self):
        c = self._make()
        notion_obj = {
            "id": "page-abc",
            "object": "page",
            "last_edited_time": "2026-01-01T00:00:00Z",
            "properties": {
                "title": [{"plain_text": "My Page"}]
            },
        }
        rf = c._to_remote_file(notion_obj)
        assert rf.path == "My Page.md"

    def test_to_remote_file_with_title_dict_prop(self):
        c = self._make()
        notion_obj = {
            "id": "page-def",
            "object": "page",
            "last_edited_time": "2026-01-01T00:00:00Z",
            "properties": {
                "title": {"title": [{"plain_text": "Dict Title"}]}
            },
        }
        rf = c._to_remote_file(notion_obj)
        assert rf.path == "Dict Title.md"

    def test_to_remote_file_untitled_fallback(self):
        c = self._make()
        rf = c._to_remote_file({"id": "x", "object": "page", "properties": {}})
        assert rf.path == "Untitled.md"

    def test_blocks_to_markdown_paragraph(self):
        c = self._make()
        blocks = [{"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Hello"}]}}]
        result = c._blocks_to_markdown(blocks)
        assert "Hello" in result

    def test_blocks_to_markdown_heading(self):
        c = self._make()
        blocks = [{"type": "heading_1", "heading_1": {"rich_text": [{"plain_text": "Title"}]}}]
        result = c._blocks_to_markdown(blocks)
        assert result.startswith("# Title")

    def test_blocks_to_markdown_bullet(self):
        c = self._make()
        blocks = [
            {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"plain_text": "Item"}]}}
        ]
        result = c._blocks_to_markdown(blocks)
        assert "- Item" in result

    def test_blocks_to_markdown_code(self):
        c = self._make()
        blocks = [
            {
                "type": "code",
                "code": {"rich_text": [{"plain_text": "print()"}], "language": "python"},
            }
        ]
        result = c._blocks_to_markdown(blocks)
        assert "```python" in result
        assert "print()" in result

    def test_blocks_to_markdown_empty(self):
        c = self._make()
        assert c._blocks_to_markdown([]) == ""

    def test_blocks_to_markdown_no_rich_text(self):
        # Block with no rich_text should produce empty string, not crash
        c = self._make()
        blocks = [{"type": "image", "image": {}}]
        result = c._blocks_to_markdown(blocks)
        assert isinstance(result, str)


# ===========================================================================
# connectors/notion_adapter.py — pure functions
# ===========================================================================


class TestNotionHeaders:
    def test_authorization_header(self):
        h = _notion_headers("my_token")
        assert h["Authorization"] == "Bearer my_token"

    def test_notion_version_present(self):
        h = _notion_headers("tok")
        assert "Notion-Version" in h

    def test_content_type_json(self):
        h = _notion_headers("tok")
        assert h["Content-Type"] == "application/json"


class TestExtractRichText:
    def test_single_item(self):
        assert _extract_rich_text([{"plain_text": "hello"}]) == "hello"

    def test_multiple_items(self):
        items = [{"plain_text": "foo"}, {"plain_text": " bar"}]
        assert _extract_rich_text(items) == "foo bar"

    def test_empty_list(self):
        assert _extract_rich_text([]) == ""

    def test_missing_plain_text_key(self):
        # Should not crash; missing key → empty string contribution
        assert _extract_rich_text([{}]) == ""


class TestBlockToText:
    def _rt(self, text):
        return [{"plain_text": text}]

    def test_paragraph(self):
        b = {"type": "paragraph", "paragraph": {"rich_text": self._rt("Hello")}}
        assert _block_to_text(b) == "Hello"

    def test_quote(self):
        b = {"type": "quote", "quote": {"rich_text": self._rt("Quoted")}}
        assert _block_to_text(b) == "Quoted"

    def test_callout(self):
        b = {"type": "callout", "callout": {"rich_text": self._rt("Note")}}
        assert _block_to_text(b) == "Note"

    def test_heading_1(self):
        b = {"type": "heading_1", "heading_1": {"rich_text": self._rt("H1")}}
        assert _block_to_text(b) == "# H1"

    def test_heading_2(self):
        b = {"type": "heading_2", "heading_2": {"rich_text": self._rt("H2")}}
        assert _block_to_text(b) == "## H2"

    def test_heading_3(self):
        b = {"type": "heading_3", "heading_3": {"rich_text": self._rt("H3")}}
        assert _block_to_text(b) == "### H3"

    def test_bulleted_list_item(self):
        b = {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": self._rt("Bullet")}}
        assert _block_to_text(b) == "- Bullet"

    def test_numbered_list_item(self):
        b = {"type": "numbered_list_item", "numbered_list_item": {"rich_text": self._rt("Num")}}
        assert _block_to_text(b) == "- Num"

    def test_to_do_unchecked(self):
        b = {"type": "to_do", "to_do": {"rich_text": self._rt("Task"), "checked": False}}
        assert _block_to_text(b) == "- [ ] Task"

    def test_to_do_checked(self):
        b = {"type": "to_do", "to_do": {"rich_text": self._rt("Done"), "checked": True}}
        assert _block_to_text(b) == "- [x] Done"

    def test_code_block(self):
        b = {"type": "code", "code": {"rich_text": self._rt("x = 1"), "language": "python"}}
        result = _block_to_text(b)
        assert result == "```python\nx = 1\n```"

    def test_divider(self):
        b = {"type": "divider", "divider": {}}
        assert _block_to_text(b) == "---"

    def test_table_row(self):
        b = {
            "type": "table_row",
            "table_row": {"cells": [self._rt("A"), self._rt("B")]},
        }
        assert _block_to_text(b) == "A | B"

    def test_image_external(self):
        b = {
            "type": "image",
            "image": {
                "external": {"url": "https://img.example.com/pic.png"},
                "caption": self._rt("Alt"),
            },
        }
        result = _block_to_text(b)
        assert "https://img.example.com/pic.png" in result
        assert "Alt" in result

    def test_image_file(self):
        b = {
            "type": "image",
            "image": {"file": {"url": "https://cdn.example.com/img.jpg"}, "caption": []},
        }
        result = _block_to_text(b)
        assert "https://cdn.example.com/img.jpg" in result

    def test_unknown_block_with_rich_text_fallback(self):
        b = {"type": "unsupported_type", "unsupported_type": {"rich_text": self._rt("Text")}}
        assert _block_to_text(b) == "Text"

    def test_unknown_block_no_rich_text_returns_empty(self):
        b = {"type": "mystery", "mystery": {}}
        assert _block_to_text(b) == ""


class TestPageTitle:
    def test_extracts_from_title_key(self):
        props = {
            "title": {
                "type": "title",
                "title": [{"plain_text": "My Page"}],
            }
        }
        assert _page_title({"properties": props}) == "My Page"

    def test_returns_empty_when_no_title_type(self):
        props = {"body": {"type": "rich_text", "rich_text": []}}
        assert _page_title({"properties": props}) == ""

    def test_returns_empty_on_no_properties(self):
        assert _page_title({}) == ""

    def test_empty_title_array_returns_empty(self):
        props = {"title": {"type": "title", "title": []}}
        assert _page_title({"properties": props}) == ""


# ===========================================================================
# NotionAdapter — network calls mocked
# ===========================================================================


class TestNotionAdapter:
    def test_token_from_constructor(self):
        adapter = NotionAdapter(api_token="direct_token")
        assert adapter._token == "direct_token"

    def test_token_from_env(self, monkeypatch):
        monkeypatch.setenv("NOTION_API_TOKEN", "env_token")
        adapter = NotionAdapter()
        assert adapter._token == "env_token"

    def test_no_token_raises_source_fetch_error(self, monkeypatch):
        monkeypatch.delenv("NOTION_API_TOKEN", raising=False)
        adapter = NotionAdapter()
        with pytest.raises(SourceFetchError, match="Notion API token required"):
            adapter.ingest("page-id")

    def test_resolve_token_uses_kwarg_over_instance(self):
        adapter = NotionAdapter(api_token="instance_token")
        token = adapter._resolve_token({"api_token": "kwarg_token"})
        assert token == "kwarg_token"

    def test_resolve_token_falls_back_to_instance(self):
        adapter = NotionAdapter(api_token="my_token")
        token = adapter._resolve_token({})
        assert token == "my_token"

    def test_source_type(self):
        assert NotionAdapter.source_type == "notion"

    def test_ingest_mocked(self, monkeypatch):
        """Full ingest path with all network calls mocked."""
        page_data = {
            "last_edited_time": "2026-01-01T12:00:00Z",
            "properties": {
                "title": {"type": "title", "title": [{"plain_text": "Test Page"}]}
            },
        }
        blocks_data = [
            {"results": [{"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Body text"}]}}], "has_more": False}
        ]
        call_count = [0]

        def mock_get(url, headers):
            if "/pages/" in url:
                return page_data
            if "/blocks/" in url:
                result = blocks_data[call_count[0]]
                call_count[0] += 1
                return result
            return {}

        import tokenpak.connectors.notion_adapter as na_mod
        monkeypatch.setattr(na_mod, "_get", mock_get)

        adapter = NotionAdapter(api_token="tok")
        content, prov = adapter.ingest("page-123")

        assert "Test Page" in content
        assert "Body text" in content
        assert prov.source_type == "notion"
        assert prov.source_id == "page-123"
        assert prov.title == "Test Page"
        assert prov.source_version == "2026-01-01T12:00:00Z"

    def test_ingest_no_title(self, monkeypatch):
        page_data = {
            "last_edited_time": "2026-01-01T12:00:00Z",
            "properties": {},
        }
        blocks_response = {"results": [], "has_more": False}

        import tokenpak.connectors.notion_adapter as na_mod
        monkeypatch.setattr(na_mod, "_get", lambda url, h: page_data if "/pages/" in url else blocks_response)

        adapter = NotionAdapter(api_token="tok")
        content, prov = adapter.ingest("page-456")
        assert prov.title == ""

    def test_has_changed_true(self, monkeypatch):
        import tokenpak.connectors.notion_adapter as na_mod
        monkeypatch.setattr(
            na_mod, "_get", lambda url, h: {"last_edited_time": "2026-02-01T00:00:00Z"}
        )
        adapter = NotionAdapter(api_token="tok")
        assert adapter.has_changed("page-1", "2026-01-01T00:00:00Z") is True

    def test_has_changed_false(self, monkeypatch):
        import tokenpak.connectors.notion_adapter as na_mod
        ts = "2026-01-01T00:00:00Z"
        monkeypatch.setattr(na_mod, "_get", lambda url, h: {"last_edited_time": ts})
        adapter = NotionAdapter(api_token="tok")
        assert adapter.has_changed("page-1", ts) is False

    def test_has_changed_returns_false_on_error(self, monkeypatch):
        import tokenpak.connectors.notion_adapter as na_mod

        def raise_error(url, h):
            raise SourceFetchError("network down")

        monkeypatch.setattr(na_mod, "_get", raise_error)
        adapter = NotionAdapter(api_token="tok")
        assert adapter.has_changed("page-1", "cached") is False
