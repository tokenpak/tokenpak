# SPDX-License-Identifier: Apache-2.0
"""Unit tests for connectors.git_adapter — GitAdapter."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from tokenpak.sources.base_source import SourceFetchError
from tokenpak.sources.git_adapter import (
    GitAdapter,
    _read_file_at_commit,
    _resolve_sha,
    _run_git,
)

_FAKE_SHA = "a" * 40  # Valid 40-char SHA


# ---------------------------------------------------------------------------
# _run_git
# ---------------------------------------------------------------------------


class TestRunGit:
    def test_returns_stdout_on_success(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="output\n", stderr="")
            result = _run_git(["log", "--oneline"], cwd="/tmp/repo")
        assert result == "output\n"

    def test_raises_on_nonzero_returncode(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=128, stdout="", stderr="fatal: not a git repo"
            )
            with pytest.raises(SourceFetchError, match="failed"):
                _run_git(["status"], cwd="/tmp/not-a-repo")

    def test_raises_when_git_not_on_path(self):
        with patch("subprocess.run", side_effect=FileNotFoundError("git not found")):
            with pytest.raises(SourceFetchError, match="git not found"):
                _run_git(["status"], cwd="/tmp")

    def test_raises_on_timeout(self):
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=15),
        ):
            with pytest.raises(SourceFetchError, match="timed out"):
                _run_git(["clone", "..."], cwd="/tmp")


# ---------------------------------------------------------------------------
# _resolve_sha
# ---------------------------------------------------------------------------


class TestResolveSha:
    def test_returns_full_sha(self):
        with patch("tokenpak.sources.git_adapter._run_git") as mock_git:
            mock_git.return_value = _FAKE_SHA + "\n"
            sha = _resolve_sha("/repo", "HEAD")
        assert sha == _FAKE_SHA

    def test_raises_on_short_sha(self):
        with patch("tokenpak.sources.git_adapter._run_git") as mock_git:
            mock_git.return_value = "abc123\n"  # Too short
            with pytest.raises(SourceFetchError, match="SHA format"):
                _resolve_sha("/repo", "HEAD")

    def test_raises_on_empty_output(self):
        with patch("tokenpak.sources.git_adapter._run_git") as mock_git:
            mock_git.return_value = "\n"
            with pytest.raises(SourceFetchError):
                _resolve_sha("/repo", "HEAD")


# ---------------------------------------------------------------------------
# _read_file_at_commit
# ---------------------------------------------------------------------------


class TestReadFileAtCommit:
    def test_returns_file_content(self):
        with patch("tokenpak.sources.git_adapter._run_git") as mock_git:
            mock_git.return_value = "line1\nline2\n"
            result = _read_file_at_commit("/repo", "src/main.py", _FAKE_SHA)
        assert result == "line1\nline2\n"
        mock_git.assert_called_once_with(
            ["show", f"{_FAKE_SHA}:src/main.py"], cwd="/repo"
        )

    def test_propagates_source_fetch_error(self):
        with patch(
            "tokenpak.sources.git_adapter._run_git",
            side_effect=SourceFetchError("not found"),
        ):
            with pytest.raises(SourceFetchError, match="not found"):
                _read_file_at_commit("/repo", "missing.py", _FAKE_SHA)


# ---------------------------------------------------------------------------
# GitAdapter.ingest()
# ---------------------------------------------------------------------------


class TestGitAdapterIngest:
    def _make_adapter(self):
        return GitAdapter()

    def test_ingest_returns_content_and_provenance(self):
        adapter = self._make_adapter()
        with patch("tokenpak.sources.git_adapter._resolve_sha") as mock_sha, \
             patch("tokenpak.sources.git_adapter._read_file_at_commit") as mock_read:
            mock_sha.return_value = _FAKE_SHA
            mock_read.return_value = "def hello(): pass\n"
            content, prov = adapter.ingest(
                "src/hello.py", repo_path="/repo", commit_sha="HEAD"
            )

        assert content == "def hello(): pass\n"
        assert prov.source_type == "git"
        assert prov.source_id == "src/hello.py"
        assert prov.source_version == _FAKE_SHA

    def test_ingest_without_repo_path_raises(self):
        adapter = self._make_adapter()
        with pytest.raises(SourceFetchError, match="repo_path is required"):
            adapter.ingest("src/file.py")

    def test_ingest_title_contains_repo_name_and_sha(self):
        adapter = self._make_adapter()
        with patch("tokenpak.sources.git_adapter._resolve_sha") as mock_sha, \
             patch("tokenpak.sources.git_adapter._read_file_at_commit") as mock_read:
            mock_sha.return_value = _FAKE_SHA
            mock_read.return_value = "content"
            _, prov = adapter.ingest("README.md", repo_path="/home/user/myrepo")

        assert "myrepo" in prov.title
        assert "README.md" in prov.title
        assert _FAKE_SHA[:8] in prov.title

    def test_ingest_uses_head_as_default_commit(self):
        adapter = self._make_adapter()
        with patch("tokenpak.sources.git_adapter._resolve_sha") as mock_sha, \
             patch("tokenpak.sources.git_adapter._read_file_at_commit") as mock_read:
            mock_sha.return_value = _FAKE_SHA
            mock_read.return_value = ""
            adapter.ingest("x.py", repo_path="/repo")
            mock_sha.assert_called_once_with("/repo", "HEAD")

    def test_ingest_custom_commit_sha(self):
        adapter = self._make_adapter()
        custom_sha = "b" * 40
        with patch("tokenpak.sources.git_adapter._resolve_sha") as mock_sha, \
             patch("tokenpak.sources.git_adapter._read_file_at_commit") as mock_read:
            mock_sha.return_value = custom_sha
            mock_read.return_value = "old content"
            _, prov = adapter.ingest("x.py", repo_path="/repo", commit_sha=custom_sha)
            mock_sha.assert_called_once_with("/repo", custom_sha)

    def test_ingest_fetched_at_is_iso_timestamp(self):
        adapter = self._make_adapter()
        with patch("tokenpak.sources.git_adapter._resolve_sha") as mock_sha, \
             patch("tokenpak.sources.git_adapter._read_file_at_commit") as mock_read:
            mock_sha.return_value = _FAKE_SHA
            mock_read.return_value = ""
            _, prov = adapter.ingest("x.py", repo_path="/repo")

        assert "T" in prov.fetched_at


# ---------------------------------------------------------------------------
# GitAdapter.has_changed()
# ---------------------------------------------------------------------------


class TestGitAdapterHasChanged:
    def _make_adapter(self):
        return GitAdapter()

    def test_returns_false_when_sha_same(self):
        adapter = self._make_adapter()
        with patch("tokenpak.sources.git_adapter._resolve_sha") as mock_sha:
            mock_sha.return_value = _FAKE_SHA
            result = adapter.has_changed("src/x.py", _FAKE_SHA, repo_path="/repo")
        assert result is False

    def test_returns_true_when_file_in_diff(self):
        adapter = self._make_adapter()
        new_sha = "b" * 40
        with patch("tokenpak.sources.git_adapter._resolve_sha") as mock_sha, \
             patch("tokenpak.sources.git_adapter._run_git") as mock_git:
            mock_sha.return_value = new_sha
            mock_git.return_value = "src/x.py\n"  # diff shows file changed
            result = adapter.has_changed("src/x.py", _FAKE_SHA, repo_path="/repo")
        assert result is True

    def test_returns_false_when_file_not_in_diff(self):
        adapter = self._make_adapter()
        new_sha = "b" * 40
        with patch("tokenpak.sources.git_adapter._resolve_sha") as mock_sha, \
             patch("tokenpak.sources.git_adapter._run_git") as mock_git:
            mock_sha.return_value = new_sha
            mock_git.return_value = ""  # No changes to this file
            result = adapter.has_changed("src/x.py", _FAKE_SHA, repo_path="/repo")
        assert result is False

    def test_returns_false_when_no_repo_path(self):
        adapter = self._make_adapter()
        result = adapter.has_changed("src/x.py", _FAKE_SHA)
        assert result is False

    def test_returns_false_on_source_fetch_error(self):
        adapter = self._make_adapter()
        with patch(
            "tokenpak.sources.git_adapter._resolve_sha",
            side_effect=SourceFetchError("git error"),
        ):
            result = adapter.has_changed("src/x.py", _FAKE_SHA, repo_path="/repo")
        assert result is False

    def test_source_type_is_git(self):
        adapter = self._make_adapter()
        assert adapter.source_type == "git"
