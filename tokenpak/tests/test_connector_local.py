# SPDX-License-Identifier: Apache-2.0
"""Unit tests for connectors.local — LocalConnector."""

import time
from pathlib import Path

import pytest

from tokenpak.sources.base import ConnectorConfig
from tokenpak.sources.local import LocalConnector


def _make_config(tmp_path, **kwargs):
    # Include both root-level and nested files (fnmatch "**/*" only matches paths with /)
    kwargs.setdefault("include_patterns", ["*", "**/*"])
    return ConnectorConfig(name="local", source_path=str(tmp_path), **kwargs)


# ---------------------------------------------------------------------------
# connect()
# ---------------------------------------------------------------------------


class TestLocalConnectorConnect:
    def test_connect_valid_directory(self, tmp_path):
        conn = LocalConnector(_make_config(tmp_path))
        assert conn.connect() is True

    def test_connect_nonexistent_path(self, tmp_path):
        cfg = ConnectorConfig(name="local", source_path=str(tmp_path / "no_such_dir"))
        conn = LocalConnector(cfg)
        assert conn.connect() is False

    def test_connect_file_not_directory(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hello")
        cfg = ConnectorConfig(name="local", source_path=str(f))
        conn = LocalConnector(cfg)
        assert conn.connect() is False


# ---------------------------------------------------------------------------
# list_files()
# ---------------------------------------------------------------------------


class TestLocalConnectorListFiles:
    def test_lists_single_file(self, tmp_path):
        (tmp_path / "note.md").write_text("# Hello")
        conn = LocalConnector(_make_config(tmp_path))
        files = list(conn.list_files())
        assert len(files) == 1
        assert files[0].path == "note.md"

    def test_lists_multiple_files(self, tmp_path):
        (tmp_path / "a.md").write_text("A")
        (tmp_path / "b.txt").write_text("B")
        conn = LocalConnector(_make_config(tmp_path))
        files = list(conn.list_files())
        paths = {f.path for f in files}
        assert paths == {"a.md", "b.txt"}

    def test_lists_nested_files(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.md").write_text("deep")
        conn = LocalConnector(_make_config(tmp_path))
        files = list(conn.list_files())
        paths = {f.path for f in files}
        assert "sub/deep.md" in paths

    def test_empty_directory(self, tmp_path):
        conn = LocalConnector(_make_config(tmp_path))
        assert list(conn.list_files()) == []

    def test_skips_subdirectories(self, tmp_path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        conn = LocalConnector(_make_config(tmp_path))
        files = list(conn.list_files())
        assert all(not Path(f.path).is_dir() for f in files)

    def test_remote_file_has_correct_size(self, tmp_path):
        content = b"hello world"
        (tmp_path / "f.txt").write_bytes(content)
        conn = LocalConnector(_make_config(tmp_path))
        files = list(conn.list_files())
        assert files[0].size_bytes == len(content)

    def test_remote_file_has_file_type(self, tmp_path):
        (tmp_path / "readme.md").write_text("hello")
        conn = LocalConnector(_make_config(tmp_path))
        files = list(conn.list_files())
        assert files[0].file_type == "md"

    def test_remote_file_source_id_is_absolute_path(self, tmp_path):
        (tmp_path / "f.txt").write_text("x")
        conn = LocalConnector(_make_config(tmp_path))
        files = list(conn.list_files())
        assert Path(files[0].source_id).is_absolute()

    def test_remote_file_modified_at_is_string(self, tmp_path):
        (tmp_path / "f.txt").write_text("x")
        conn = LocalConnector(_make_config(tmp_path))
        files = list(conn.list_files())
        assert isinstance(files[0].modified_at, str)
        assert "T" in files[0].modified_at


# ---------------------------------------------------------------------------
# Exclude patterns
# ---------------------------------------------------------------------------


class TestLocalConnectorExcludePatterns:
    def test_exclude_pattern_skips_matching_file(self, tmp_path):
        (tmp_path / "keep.md").write_text("keep")
        (tmp_path / "skip.log").write_text("skip")
        cfg = _make_config(tmp_path, exclude_patterns=["*.log"])
        conn = LocalConnector(cfg)
        files = list(conn.list_files())
        paths = {f.path for f in files}
        assert "keep.md" in paths
        assert "skip.log" not in paths

    def test_exclude_pattern_glob_nested(self, tmp_path):
        sub = tmp_path / "cache"
        sub.mkdir()
        (sub / "data.json").write_text("{}")
        (tmp_path / "main.py").write_text("x")
        cfg = _make_config(tmp_path, exclude_patterns=["cache/*"])
        conn = LocalConnector(cfg)
        files = list(conn.list_files())
        paths = {f.path for f in files}
        assert "main.py" in paths
        assert "cache/data.json" not in paths

    def test_no_excludes_keeps_all(self, tmp_path):
        (tmp_path / "a.md").write_text("a")
        (tmp_path / "b.log").write_text("b")
        cfg = _make_config(tmp_path, exclude_patterns=[])
        conn = LocalConnector(cfg)
        files = list(conn.list_files())
        assert len(files) == 2


# ---------------------------------------------------------------------------
# Include patterns
# ---------------------------------------------------------------------------


class TestLocalConnectorIncludePatterns:
    def test_include_pattern_filters_to_matching(self, tmp_path):
        (tmp_path / "a.md").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        cfg = _make_config(tmp_path, include_patterns=["*.md"])
        conn = LocalConnector(cfg)
        files = list(conn.list_files())
        paths = {f.path for f in files}
        assert paths == {"a.md"}

    def test_include_wildcard_matches_all(self, tmp_path):
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "x.py").write_text("x")
        (sub / "y.json").write_text("{}")
        cfg = _make_config(tmp_path, include_patterns=["**/*"])
        conn = LocalConnector(cfg)
        files = list(conn.list_files())
        assert len(files) == 2

    def test_include_pattern_no_match_returns_empty(self, tmp_path):
        (tmp_path / "a.md").write_text("a")
        cfg = _make_config(tmp_path, include_patterns=["*.xyz"])
        conn = LocalConnector(cfg)
        assert list(conn.list_files()) == []


# ---------------------------------------------------------------------------
# max_file_size_mb
# ---------------------------------------------------------------------------


class TestLocalConnectorMaxFileSize:
    def test_skips_oversized_file(self, tmp_path):
        # Write 2 bytes, set max to essentially 0 MB
        big = tmp_path / "big.bin"
        big.write_bytes(b"x" * 1024)  # 1 KB
        small = tmp_path / "small.txt"
        small.write_text("hi")
        # Max file size = 0 MB → 0 bytes threshold, all files skipped
        cfg = _make_config(tmp_path, max_file_size_mb=0)
        conn = LocalConnector(cfg)
        files = list(conn.list_files())
        assert all(f.size_bytes == 0 for f in files)  # 0-byte files pass

    def test_includes_file_within_limit(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("hello")  # tiny
        cfg = _make_config(tmp_path, max_file_size_mb=10)
        conn = LocalConnector(cfg)
        files = list(conn.list_files())
        assert len(files) == 1


# ---------------------------------------------------------------------------
# Delta sync via `since` parameter
# ---------------------------------------------------------------------------


class TestLocalConnectorDeltaSync:
    def test_since_excludes_older_files(self, tmp_path):
        old = tmp_path / "old.md"
        old.write_text("old")
        # Set modification time to 1 hour ago
        old_ts = time.time() - 3600
        import os
        os.utime(str(old), (old_ts, old_ts))

        new = tmp_path / "new.md"
        new.write_text("new")

        # `since` is 30 minutes ago
        from datetime import datetime, timedelta
        since = (datetime.now() - timedelta(minutes=30)).isoformat()

        conn = LocalConnector(_make_config(tmp_path))
        files = list(conn.list_files(since=since))
        paths = {f.path for f in files}
        assert "new.md" in paths
        assert "old.md" not in paths

    def test_without_since_returns_all(self, tmp_path):
        (tmp_path / "a.md").write_text("a")
        (tmp_path / "b.md").write_text("b")
        conn = LocalConnector(_make_config(tmp_path))
        assert len(list(conn.list_files(since=None))) == 2


# ---------------------------------------------------------------------------
# get_content()
# ---------------------------------------------------------------------------


class TestLocalConnectorGetContent:
    def test_get_content_returns_bytes(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_bytes(b"hello bytes")
        conn = LocalConnector(_make_config(tmp_path))
        files = list(conn.list_files())
        content = conn.get_content(files[0])
        assert content == b"hello bytes"

    def test_get_content_binary_file(self, tmp_path):
        data = bytes(range(256))
        f = tmp_path / "binary.bin"
        f.write_bytes(data)
        conn = LocalConnector(_make_config(tmp_path))
        files = list(conn.list_files())
        assert conn.get_content(files[0]) == data

    def test_get_content_uses_source_id(self, tmp_path):
        """get_content reads via source_id (absolute path), not relative path."""
        f = tmp_path / "sub" / "note.md"
        f.parent.mkdir()
        f.write_bytes(b"deep content")
        conn = LocalConnector(_make_config(tmp_path))
        files = list(conn.list_files())
        assert conn.get_content(files[0]) == b"deep content"
