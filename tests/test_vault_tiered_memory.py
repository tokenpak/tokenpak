"""
Unit tests for VaultIndex tiered memory (LRU content cache).
Tests: _get_content(), _enforce_cache_limit(), cache hit/miss/eviction, cache_stats.
"""
import json
import threading
import time
from collections import OrderedDict
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — build a minimal VaultIndex without importing the full proxy
# ---------------------------------------------------------------------------

def make_vault_index(tmp_path: Path, max_bytes: int = 1024 * 1024):
    """Create a VaultIndex pointed at a temp tokenpak dir."""
    # Minimal import — proxy.py is a flat module, we import just what we need
    import importlib, sys, types

    # Patch config constants before import if not already imported
    import proxy as px

    idx = px.VaultIndex.__new__(px.VaultIndex)
    # Manually init to avoid file I/O
    idx.tokenpak_dir = tmp_path
    idx.blocks = {}
    idx._last_loaded = 0
    idx._last_mtime = 0
    idx._lock = threading.Lock()
    idx._df = {}
    idx._block_tfs = {}
    idx._avg_dl = 0.0
    idx._doc_count = 0
    idx._content_cache = OrderedDict()
    idx._cache_bytes = 0
    idx._max_cache_bytes = max_bytes
    idx._cache_hits = 0
    idx._cache_misses = 0
    idx._cache_evictions = 0
    return idx


def populate_block(tmp_path: Path, idx, block_id: str, content: str):
    """Add a block to idx.blocks and write content to disk."""
    blocks_dir = tmp_path / "blocks"
    blocks_dir.mkdir(exist_ok=True)
    content_file = blocks_dir / f"{block_id}.txt"
    content_file.write_text(content, encoding="utf-8")
    idx.blocks[block_id] = {
        "block_id": block_id,
        "source_path": f"test/{block_id}",
        "risk_class": "narrative",
        "must_keep": False,
        "raw_tokens": len(content.split()),
        "_content_file": str(content_file),
    }
    return content_file


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGetContent:
    def test_cache_miss_reads_disk(self, tmp_path):
        idx = make_vault_index(tmp_path)
        populate_block(tmp_path, idx, "b1", "hello world")
        content = idx._get_content("b1")
        assert content == "hello world"
        assert idx._cache_misses == 1
        assert idx._cache_hits == 0
        assert "b1" in idx._content_cache

    def test_cache_hit_after_first_read(self, tmp_path):
        idx = make_vault_index(tmp_path)
        populate_block(tmp_path, idx, "b1", "hello world")
        idx._get_content("b1")  # miss
        content = idx._get_content("b1")  # hit
        assert content == "hello world"
        assert idx._cache_hits == 1
        assert idx._cache_misses == 1

    def test_missing_block_returns_empty(self, tmp_path):
        idx = make_vault_index(tmp_path)
        content = idx._get_content("nonexistent")
        assert content == ""

    def test_missing_file_returns_empty(self, tmp_path):
        idx = make_vault_index(tmp_path)
        # Register block but point to non-existent file
        idx.blocks["ghost"] = {
            "block_id": "ghost",
            "source_path": "test/ghost",
            "risk_class": "narrative",
            "must_keep": False,
            "raw_tokens": 0,
            "_content_file": str(tmp_path / "blocks" / "ghost.txt"),
        }
        content = idx._get_content("ghost")
        assert content == ""
        assert idx._cache_misses == 1

    def test_lru_order_updates_on_hit(self, tmp_path):
        idx = make_vault_index(tmp_path)
        populate_block(tmp_path, idx, "b1", "first")
        populate_block(tmp_path, idx, "b2", "second")
        idx._get_content("b1")  # b1 added
        idx._get_content("b2")  # b2 added
        # Access b1 again — should move to end (most recently used)
        idx._get_content("b1")
        keys = list(idx._content_cache.keys())
        assert keys[-1] == "b1", "b1 should be MRU after access"


class TestEnforceCacheLimit:
    def test_evicts_lru_when_over_limit(self, tmp_path):
        # 50-byte limit — each block ~10 bytes
        idx = make_vault_index(tmp_path, max_bytes=50)
        for i in range(10):
            block_id = f"b{i}"
            content = f"block{i:03d}"  # ~8 bytes each
            populate_block(tmp_path, idx, block_id, content)
            idx._get_content(block_id)  # populate cache

        # Cache should be within limit
        assert idx._cache_bytes <= 50
        assert idx._cache_evictions > 0

    def test_no_eviction_when_within_limit(self, tmp_path):
        idx = make_vault_index(tmp_path, max_bytes=1024 * 1024)
        for i in range(5):
            populate_block(tmp_path, idx, f"b{i}", f"content{i}")
            idx._get_content(f"b{i}")
        assert idx._cache_evictions == 0


class TestCacheStats:
    def test_stats_structure(self, tmp_path):
        idx = make_vault_index(tmp_path)
        stats = idx.cache_stats
        assert "vault_cache_entries" in stats
        assert "vault_cache_memory_mb" in stats
        assert "vault_cache_hits" in stats
        assert "vault_cache_misses" in stats
        assert "vault_cache_evictions" in stats
        assert "vault_cache_hit_rate" in stats

    def test_hit_rate_calculation(self, tmp_path):
        idx = make_vault_index(tmp_path)
        populate_block(tmp_path, idx, "b1", "data")
        idx._get_content("b1")  # miss
        idx._get_content("b1")  # hit
        idx._get_content("b1")  # hit
        stats = idx.cache_stats
        assert stats["vault_cache_hit_rate"] == pytest.approx(2 / 3, rel=1e-3)

    def test_zero_hit_rate_when_no_requests(self, tmp_path):
        idx = make_vault_index(tmp_path)
        stats = idx.cache_stats
        assert stats["vault_cache_hit_rate"] == 0.0


class TestLoad:
    def test_load_does_not_store_content_in_blocks(self, tmp_path):
        """After _load(), blocks should NOT have 'content' key."""
        import proxy as px

        # Build minimal tokenpak dir
        tokenpak_dir = tmp_path / ".tokenpak"
        tokenpak_dir.mkdir()
        blocks_dir = tokenpak_dir / "blocks"
        blocks_dir.mkdir()

        block_data = {}
        for i in range(3):
            bid = f"block{i:03d}"
            (blocks_dir / f"{bid}.txt").write_text(f"content for {bid}", encoding="utf-8")
            block_data[bid] = {"source_path": f"test/{bid}", "raw_tokens": 3}

        index_data = {"blocks": block_data}
        index_path = tokenpak_dir / "index.json"
        index_path.write_text(json.dumps(index_data), encoding="utf-8")

        with patch.object(px, "VAULT_CACHE_PRELOAD", 0):
            idx = px.VaultIndex(str(tokenpak_dir))
            idx._max_cache_bytes = 1024 * 1024
            idx._load(index_path, index_path.stat().st_mtime)

        # Blocks should exist but NOT have 'content' key
        assert len(idx.blocks) == 3
        for bid, block in idx.blocks.items():
            assert "content" not in block, f"Block {bid} should not have 'content' key"
            assert "_content_file" in block

    def test_load_preloads_top_n_blocks(self, tmp_path):
        """After _load() with VAULT_CACHE_PRELOAD=2, cache should have 2 entries."""
        import proxy as px

        tokenpak_dir = tmp_path / ".tokenpak"
        tokenpak_dir.mkdir()
        blocks_dir = tokenpak_dir / "blocks"
        blocks_dir.mkdir()

        block_data = {}
        for i in range(5):
            bid = f"block{i:03d}"
            (blocks_dir / f"{bid}.txt").write_text(f"content for {bid}", encoding="utf-8")
            block_data[bid] = {"source_path": f"test/{bid}", "raw_tokens": 3}

        index_path = tokenpak_dir / "index.json"
        index_path.write_text(json.dumps({"blocks": block_data}), encoding="utf-8")

        with patch.object(px, "VAULT_CACHE_PRELOAD", 2):
            idx = px.VaultIndex(str(tokenpak_dir))
            idx._max_cache_bytes = 1024 * 1024
            idx._load(index_path, index_path.stat().st_mtime)

        assert len(idx._content_cache) == 2


class TestCompileInjectionWithCache:
    def test_compile_injection_uses_get_content(self, tmp_path):
        """compile_injection() should use _get_content(), populating cache."""
        import proxy as px

        tokenpak_dir = tmp_path / ".tokenpak"
        tokenpak_dir.mkdir()
        blocks_dir = tokenpak_dir / "blocks"
        blocks_dir.mkdir()

        # Write a block with searchable content
        bid = "abc123"
        content = "tokenpak vault memory cache performance optimization"
        (blocks_dir / f"{bid}.txt").write_text(content, encoding="utf-8")

        with patch.object(px, "VAULT_CACHE_PRELOAD", 0):
            idx = px.VaultIndex.__new__(px.VaultIndex)
            idx.tokenpak_dir = tokenpak_dir
            idx.blocks = {
                bid: {
                    "block_id": bid,
                    "source_path": "test/abc123",
                    "risk_class": "narrative",
                    "must_keep": False,
                    "raw_tokens": 10,
                    "_content_file": str(blocks_dir / f"{bid}.txt"),
                }
            }
            idx._last_loaded = 0
            idx._last_mtime = 0
            idx._lock = threading.Lock()
            idx._df = {"tokenpak": 1, "vault": 1, "memory": 1, "cache": 1}
            idx._block_tfs = {bid: {"tokenpak": 1, "vault": 1, "memory": 1, "cache": 1}}
            idx._avg_dl = 10.0
            idx._doc_count = 1
            idx._content_cache = OrderedDict()
            idx._cache_bytes = 0
            idx._max_cache_bytes = 1024 * 1024
            idx._cache_hits = 0
            idx._cache_misses = 0
            idx._cache_evictions = 0

        injection, tokens, refs = idx.compile_injection("tokenpak vault memory", budget=1000, min_score=0.1)
        assert content in injection
        assert idx._cache_misses >= 1  # content was fetched from disk
