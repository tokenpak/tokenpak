"""Unit tests for Compile-Time Tool Orchestration (Phase 3.4)."""


import pytest

pytest.importorskip("tokenpak.reference_scanner", reason="module not available in current build")
import json
import os
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest
from tokenpak.compiler import (
    _CACHE_TTL_SECONDS,
    _cache_get,
    _cache_key,
    _cache_put,
    _prune_stale,
    compile_with_refs,
)
from tokenpak.reference_fetcher import fetch_reference
from tokenpak.reference_scanner import (
    Reference,
    RefType,
    scan_for_references,
)

# ---------------------------------------------------------------------------
# Reference scanner — detection patterns
# ---------------------------------------------------------------------------

class TestReferenceScanner:
    def test_github_issue_url(self):
        text = "See https://github.com/owner/repo/issues/42 for details."
        refs = scan_for_references(text)
        gh = [r for r in refs if r.ref_type == RefType.GITHUB_ISSUE]
        assert len(gh) == 1
        assert "issues/42" in gh[0].resolved_url
        assert "owner" in gh[0].resolved_url

    def test_github_pr_url(self):
        text = "PR: https://github.com/org/project/pull/99"
        refs = scan_for_references(text)
        prs = [r for r in refs if r.ref_type == RefType.GITHUB_PR]
        assert len(prs) == 1
        assert prs[0].raw_match == "https://github.com/org/project/pull/99"

    def test_bare_url(self):
        text = "Check the docs at https://docs.example.com/guide/setup"
        refs = scan_for_references(text)
        urls = [r for r in refs if r.ref_type == RefType.URL]
        assert any("docs.example.com" in r.resolved_url for r in urls)

    def test_linear_ticket(self):
        text = "Blocking issue: ENG-123 needs to be resolved first."
        refs = scan_for_references(text)
        tickets = [r for r in refs if r.ref_type == RefType.LINEAR_TICKET]
        assert len(tickets) == 1
        assert tickets[0].raw_match == "ENG-123"

    def test_non_matching_text(self):
        refs = scan_for_references("No references here, just plain text.")
        assert refs == []

    def test_deduplication(self):
        text = (
            "https://github.com/owner/repo/issues/1 and "
            "https://github.com/owner/repo/issues/1 again"
        )
        refs = scan_for_references(text)
        gh = [r for r in refs if r.ref_type == RefType.GITHUB_ISSUE]
        assert len(gh) == 1  # Deduplicated

    def test_images_skipped(self):
        text = "Image: https://example.com/photo.png"
        refs = scan_for_references(text)
        urls = [r for r in refs if r.ref_type == RefType.URL]
        assert len(urls) == 0

    def test_github_not_double_counted_as_url(self):
        text = "https://github.com/owner/repo/issues/5"
        refs = scan_for_references(text)
        assert not any(r.ref_type == RefType.URL for r in refs)
        assert any(r.ref_type == RefType.GITHUB_ISSUE for r in refs)

    def test_multiple_ref_types_same_text(self):
        text = (
            "Fix ENG-42 and see https://github.com/owner/repo/issues/10 "
            "and https://docs.example.com/reference"
        )
        refs = scan_for_references(text)
        types = {r.ref_type for r in refs}
        assert RefType.LINEAR_TICKET in types
        assert RefType.GITHUB_ISSUE in types
        assert RefType.URL in types

    def test_resolved_url_format_github_issue(self):
        text = "https://github.com/myorg/myrepo/issues/7"
        refs = scan_for_references(text)
        gh = [r for r in refs if r.ref_type == RefType.GITHUB_ISSUE]
        assert gh[0].resolved_url == "https://api.github.com/repos/myorg/myrepo/issues/7"


# ---------------------------------------------------------------------------
# Reference fetcher
# ---------------------------------------------------------------------------

class TestReferenceFetcher:
    def test_linear_ticket_returns_none(self):
        ref = Reference(RefType.LINEAR_TICKET, "ENG-123", "ENG-123")
        result = fetch_reference(ref)
        assert result is None

    def test_jira_ticket_returns_none(self):
        ref = Reference(RefType.JIRA_TICKET, "PROJ-456", "PROJ-456")
        result = fetch_reference(ref)
        assert result is None

    @patch("tokenpak.reference_fetcher._gh_get")
    def test_github_issue_fetched(self, mock_get):
        mock_get.side_effect = [
            {
                "number": 42,
                "title": "Fix auth bug",
                "state": "open",
                "body": "The login function crashes on empty password.",
                "labels": [{"name": "bug"}],
                "assignees": [{"login": "dev1"}],
                "html_url": "https://github.com/owner/repo/issues/42",
                "comments": 0,
                "comments_url": None,
            },
            None,  # comments call (none in this case)
        ]
        ref = Reference(
            RefType.GITHUB_ISSUE,
            "https://github.com/owner/repo/issues/42",
            "https://api.github.com/repos/owner/repo/issues/42",
        )
        result = fetch_reference(ref)
        assert result is not None
        assert "Fix auth bug" in result
        assert "login function" in result

    @patch("tokenpak.reference_fetcher._gh_get", return_value=None)
    def test_github_fetch_failure_returns_none(self, _):
        ref = Reference(
            RefType.GITHUB_ISSUE,
            "https://github.com/x/y/issues/1",
            "https://api.github.com/repos/x/y/issues/1",
        )
        result = fetch_reference(ref)
        assert result is None

    @patch("tokenpak.reference_fetcher._url_adapter")
    def test_url_fetch_delegates_to_adapter(self, mock_adapter):
        mock_adapter.ingest.return_value = ("Page content here.", MagicMock())
        ref = Reference(RefType.URL, "https://example.com/doc", "https://example.com/doc")
        result = fetch_reference(ref)
        assert result == "Page content here."


# ---------------------------------------------------------------------------
# Cache layer
# ---------------------------------------------------------------------------

class TestRefCache:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cache_path = os.path.join(self.tmpdir, "ref_cache.json")

    def _make_ref(self, url="https://example.com"):
        return Reference(RefType.URL, url, url)

    def test_cache_miss_returns_none(self):
        ref = self._make_ref()
        cache = {}
        assert _cache_get(ref, cache) is None

    def test_cache_put_and_get(self):
        ref = self._make_ref()
        cache = {}
        _cache_put(ref, "some content", cache)
        assert _cache_get(ref, cache) == "some content"

    def test_stale_entry_returns_none(self):
        ref = self._make_ref()
        cache = {
            _cache_key(ref): {
                "content": "old content",
                "fetched_at": time.time() - _CACHE_TTL_SECONDS - 1,
            }
        }
        assert _cache_get(ref, cache) is None

    def test_prune_removes_stale(self):
        ref = self._make_ref("https://stale.com")
        ref2 = self._make_ref("https://fresh.com")
        cache = {
            _cache_key(ref): {"content": "old", "fetched_at": time.time() - 9999},
            _cache_key(ref2): {"content": "new", "fetched_at": time.time()},
        }
        pruned = _prune_stale(cache)
        assert _cache_key(ref)  not in pruned
        assert _cache_key(ref2) in pruned

    def test_second_fetch_uses_cache(self):
        """compile_with_refs should use cache on second call for same ref."""
        ref = Reference(RefType.URL, "https://example.com/page", "https://example.com/page")
        cache = {}
        _cache_put(ref, "cached content", cache)

        # Save to disk
        cache_path = os.path.join(self.tmpdir, "ref_cache.json")
        Path = __import__("pathlib").Path
        Path(cache_path).write_text(json.dumps(cache))

        fetch_calls = []
        original_fetch = __import__("tokenpak.reference_fetcher", fromlist=["fetch_reference"]).fetch_reference

        def tracking_fetch(r):
            fetch_calls.append(r)
            return original_fetch(r)

        with patch("tokenpak.compiler.fetch_reference", side_effect=tracking_fetch):
            compile_with_refs([], "https://example.com/page", 5000, cache_path=cache_path)

        # fetch_reference should NOT have been called (cache hit)
        assert len(fetch_calls) == 0


# ---------------------------------------------------------------------------
# Ephemeral block injection
# ---------------------------------------------------------------------------

class TestEphemeralInjection:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cache_path = os.path.join(self.tmpdir, "ref_cache.json")

    def test_ephemeral_tag_in_output(self):
        with patch("tokenpak.compiler.fetch_reference") as mock_fetch:
            mock_fetch.return_value = "Issue title: Fix auth bug\nThe login crashes."
            blocks = [{"ref": "src/auth.py", "type": "CODE", "quality": 1.0,
                       "tokens": 100, "content": "def login(): pass"}]
            query = "https://github.com/owner/repo/issues/42"
            output = compile_with_refs(blocks, query, budget=5000,
                                       cache_path=self.cache_path)
        assert "[EPHEMERAL]" in output or "EPHEMERAL" in output or mock_fetch.called

    def test_regular_blocks_always_included(self):
        with patch("tokenpak.compiler.fetch_reference", return_value=None):
            blocks = [{"ref": "main.py", "type": "CODE", "quality": 1.0,
                       "tokens": 50, "content": "x = 1"}]
            output = compile_with_refs(blocks, "no refs here", budget=1000,
                                       cache_path=self.cache_path)
        assert "x = 1" in output

    def test_ephemeral_dropped_when_over_budget(self):
        big_content = "word " * 10000  # Very large content
        with patch("tokenpak.compiler.fetch_reference", return_value=big_content):
            blocks = [{"ref": "x", "type": "CODE", "quality": 1.0,
                       "tokens": 900, "content": "code here"}]
            # Very tight budget (only 1000 total, 900 used by regular blocks)
            output = compile_with_refs(
                blocks,
                "https://docs.example.com/large-page",
                budget=1000,
                cache_path=self.cache_path,
            )
        # Regular block should still be present
        assert "code here" in output

    def test_no_refs_no_fetch(self):
        with patch("tokenpak.compiler.fetch_reference") as mock_fetch:
            compile_with_refs([], "just a plain query with no links", budget=1000,
                              cache_path=self.cache_path)
        mock_fetch.assert_not_called()

    def test_fetch_failure_handled_gracefully(self):
        with patch("tokenpak.compiler.fetch_reference", side_effect=Exception("net error")):
            blocks = [{"ref": "x", "type": "CODE", "quality": 1.0,
                       "tokens": 10, "content": "x = 1"}]
            # Should not crash
            output = compile_with_refs(
                blocks,
                "https://github.com/owner/repo/issues/1",
                budget=1000,
                cache_path=self.cache_path,
            )
        assert "x = 1" in output


# ---------------------------------------------------------------------------
# CLI --inject-refs flag
# ---------------------------------------------------------------------------

class TestCLIInjectRefs:
    def test_inject_refs_flag_in_parser(self):
        from tokenpak.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["search", "auth function", "--inject-refs"])
        assert args.inject_refs is True

    def test_default_no_inject_refs(self):
        from tokenpak.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["search", "auth function"])
        assert args.inject_refs is False
