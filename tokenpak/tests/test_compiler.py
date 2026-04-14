# SPDX-License-Identifier: Apache-2.0
"""Unit tests for compiler.py — compile-time reference injection."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tokenpak.compression.compiler import (
    compile_with_refs,
    _build_ephemeral_block,
    _cache_get,
    _cache_key,
    _cache_put,
    _estimate_tokens,
    _load_cache,
    _prune_stale,
    _save_cache,
)
from tokenpak.compression.reference_scanner import Reference


class TestTokenEstimation:
    """Test token estimation for ephemeral blocks."""

    def test_estimate_tokens_empty(self):
        """Empty text should estimate at least 1 token."""
        assert _estimate_tokens("") == 1

    def test_estimate_tokens_simple(self):
        """Simple text estimation."""
        text = "hello world"  # 11 chars * 0.25 = 2.75 -> 2
        result = _estimate_tokens(text)
        assert result >= 1
        assert result == int(len(text) * 0.25) or result == int(len(text) * 0.25) + 1

    def test_estimate_tokens_large(self):
        """Large text should scale proportionally."""
        text = "x" * 1000
        result = _estimate_tokens(text)
        expected = int(1000 * 0.25)
        assert result == expected


class TestCacheLayer:
    """Test cache load/save/get/put operations."""

    def test_load_cache_missing_file(self):
        """Missing cache file returns empty dict."""
        result = _load_cache("/nonexistent/path/cache.json")
        assert result == {}

    def test_load_cache_existing_file(self):
        """Existing cache file is loaded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "cache.json"
            data = {"key1": {"content": "value1", "fetched_at": 0}}
            cache_path.write_text(json.dumps(data))

            result = _load_cache(str(cache_path))
            assert result == data

    def test_load_cache_corrupted_json(self):
        """Corrupted JSON returns empty dict."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "cache.json"
            cache_path.write_text("{invalid json")

            result = _load_cache(str(cache_path))
            assert result == {}

    def test_save_cache_creates_parent(self):
        """save_cache creates parent directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "subdir" / "cache.json"
            data = {"key": "value"}
            _save_cache(data, str(cache_path))

            assert cache_path.exists()
            assert json.loads(cache_path.read_text()) == data

    def test_cache_key_generation(self):
        """_cache_key generates consistent keys."""
        ref = Reference(
            ref_type="github_issue",
            raw_match="#123",
            resolved_url="https://github.com/org/repo/issues/123"
        )
        key = _cache_key(ref)
        assert key == "github_issue:https://github.com/org/repo/issues/123"

    def test_cache_get_hit(self):
        """_cache_get returns cached content if not stale."""
        ref = Reference(
            ref_type="url",
            raw_match="http://example.com",
            resolved_url="http://example.com"
        )
        cache = {
            _cache_key(ref): {
                "content": "cached content",
                "fetched_at": 0  # Very old, but testing basic get
            }
        }
        # With old fetched_at, this will actually return None due to TTL
        # Let's test with fresh timestamp
        cache[_cache_key(ref)]["fetched_at"] = __import__('time').time()
        result = _cache_get(ref, cache)
        assert result == "cached content"

    def test_cache_get_miss(self):
        """_cache_get returns None for missing key."""
        ref = Reference(
            ref_type="url",
            raw_match="http://example.com",
            resolved_url="http://example.com"
        )
        cache = {}
        result = _cache_get(ref, cache)
        assert result is None

    def test_cache_get_stale(self):
        """_cache_get returns None if entry is stale."""
        import time
        ref = Reference(
            ref_type="url",
            raw_match="http://example.com",
            resolved_url="http://example.com"
        )
        old_time = time.time() - 5000  # 5000 seconds in the past
        cache = {
            _cache_key(ref): {
                "content": "stale content",
                "fetched_at": old_time
            }
        }
        result = _cache_get(ref, cache)
        assert result is None

    def test_cache_put(self):
        """_cache_put stores content with timestamp."""
        import time
        ref = Reference(
            ref_type="url",
            raw_match="http://example.com",
            resolved_url="http://example.com"
        )
        cache = {}
        before = time.time()
        _cache_put(ref, "new content", cache)
        after = time.time()

        key = _cache_key(ref)
        assert key in cache
        assert cache[key]["content"] == "new content"
        assert before <= cache[key]["fetched_at"] <= after

    def test_prune_stale(self):
        """_prune_stale removes expired entries."""
        import time
        now = time.time()
        cache = {
            "fresh": {"content": "x", "fetched_at": now},
            "stale": {"content": "y", "fetched_at": now - 5000},
        }
        result = _prune_stale(cache)
        assert "fresh" in result
        assert "stale" not in result


class TestEphemeralBlockBuilder:
    """Test ephemeral block construction."""

    def test_build_ephemeral_block(self):
        """_build_ephemeral_block wraps content correctly."""
        ref = Reference(
            ref_type="github_issue",
            raw_match="#456",
            resolved_url="https://github.com/org/repo/issues/456"
        )
        content = "Issue description here"
        block = _build_ephemeral_block(ref, content)

        assert block["ref"] == "#456"
        assert block["type"] == "EPHEMERAL"
        assert block["content"] == content
        assert block["ephemeral"] is True
        assert block["quality"] == 0.8
        assert block["tokens"] > 0
        assert "slice_id" in block

    def test_build_ephemeral_block_token_count(self):
        """Ephemeral block token count is reasonable."""
        ref = Reference(
            ref_type="url",
            raw_match="http://example.com",
            resolved_url="http://example.com"
        )
        content = "x" * 100
        block = _build_ephemeral_block(ref, content)
        
        # 100 chars * 0.25 = 25 tokens
        assert block["tokens"] == int(100 * 0.25)


class TestCompileWithRefs:
    """Test the main compile_with_refs public API."""

    def test_compile_without_refs(self):
        """compile_with_refs with _inject_refs=False skips reference fetching."""
        blocks = [{"content": "test", "tokens": 10}]
        query = "test query"
        
        with patch("tokenpak.compression.compiler.pack") as mock_pack:
            mock_pack.return_value = "packed result"
            result = compile_with_refs(
                blocks,
                query,
                budget=1000,
                _inject_refs=False
            )
            
            assert result == "packed result"
            mock_pack.assert_called_once_with(blocks, 1000)

    def test_compile_empty_blocks(self):
        """compile_with_refs handles empty block list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "cache.json"
            
            with patch("tokenpak.compression.compiler.scan_for_references", return_value=[]):
                with patch("tokenpak.compression.compiler.pack") as mock_pack:
                    mock_pack.return_value = "packed"
                    result = compile_with_refs(
                        [],
                        "query",
                        budget=100,
                        cache_path=str(cache_path)
                    )
                    
                    assert result == "packed"

    def test_compile_with_cached_refs(self):
        """compile_with_refs uses cached references."""
        ref = Reference(
            ref_type="url",
            raw_match="http://example.com",
            resolved_url="http://example.com"
        )
        
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "cache.json"
            cache = {
                _cache_key(ref): {
                    "content": "cached content",
                    "fetched_at": __import__('time').time()
                }
            }
            _save_cache(cache, str(cache_path))
            
            blocks = [{"content": "test", "tokens": 50}]
            
            with patch("tokenpak.compression.compiler.scan_for_references", return_value=[ref]):
                with patch("tokenpak.compression.compiler.pack") as mock_pack:
                    mock_pack.return_value = "packed"
                    result = compile_with_refs(
                        blocks,
                        "query with http://example.com",
                        budget=1000,
                        cache_path=str(cache_path)
                    )
                    
                    assert result == "packed"
                    # Verify pack was called with blocks + ephemeral
                    args, kwargs = mock_pack.call_args
                    all_blocks = args[0]
                    assert len(all_blocks) > len(blocks)  # Ephemeral added

    def test_compile_budget_allocation(self):
        """compile_with_refs respects token budget."""
        ref = Reference(
            ref_type="url",
            raw_match="http://example.com",
            resolved_url="http://example.com"
        )
        
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "cache.json"
            
            # Create blocks that use most of budget
            blocks = [
                {"content": "a" * 100, "tokens": 400},  # Uses 400/500
            ]
            
            refs = [ref]
            fetched_content = "x" * 200  # Would be 50 tokens
            
            with patch("tokenpak.compression.compiler.scan_for_references", return_value=refs):
                with patch("tokenpak.compression.compiler.fetch_reference") as mock_fetch:
                    mock_fetch.return_value = fetched_content
                    with patch("tokenpak.compression.compiler.pack") as mock_pack:
                        mock_pack.return_value = "packed"
                        result = compile_with_refs(
                            blocks,
                            "query with http://example.com",
                            budget=500,
                            cache_path=str(cache_path)
                        )
                        
                        # Verify pack was called
                        args, kwargs = mock_pack.call_args
                        all_blocks = args[0]
                        budget_arg = args[1]
                        
                        assert budget_arg == 500
                        # Check if ephemeral was included (50 tokens fit in 100 remaining)
                        total_tokens = sum(b.get("tokens", 0) for b in all_blocks)
                        assert total_tokens <= 500
