"""
test_server_unit.py — Unit tests for TokenPak server infrastructure modules.

Covers:
  - tokenpak.server.websocket_proxy  (WebSocketConnectionManager, stats, compression)
  - tokenpak.proxy.cache             (LRUCache, TTL, LRU eviction, metrics)
  - tokenpak.proxy.stats             (StatsCollector, model normalisation, singleton)

All tests are self-contained: no network I/O, no file I/O, mock time where needed.
"""

from __future__ import annotations

import time

import pytest

from tokenpak.proxy.cache import LRUCache
from tokenpak.proxy.stats import StatsCollector

# ──────────────────────────────────────────────────────────────────────────────
# Imports
# ──────────────────────────────────────────────────────────────────────────────
from tokenpak.server.websocket_proxy import (
    WebSocketConnectionManager,
    WebSocketConnectionStats,
    compress_chunk,
    decompress_chunk,
)

# ══════════════════════════════════════════════════════════════════════════════
# 1. Compression utilities
# ══════════════════════════════════════════════════════════════════════════════


class TestCompressionUtils:
    def test_compress_and_decompress_string(self):
        original = "hello tokenpak"
        compressed = compress_chunk(original)
        assert isinstance(compressed, bytes)
        assert decompress_chunk(compressed) == original

    def test_compress_and_decompress_bytes(self):
        data = b"binary data \x00\x01\x02"
        compressed = compress_chunk(data)
        assert decompress_chunk(compressed) == data.decode("utf-8")

    def test_compressed_size_smaller_for_large_input(self):
        data = "a" * 10_000
        compressed = compress_chunk(data)
        assert len(compressed) < len(data)

    def test_decompress_invalid_raises(self):
        with pytest.raises(Exception):
            decompress_chunk(b"not gzip data")

    def test_compress_empty_string(self):
        compressed = compress_chunk("")
        assert decompress_chunk(compressed) == ""


# ══════════════════════════════════════════════════════════════════════════════
# 2. WebSocketConnectionStats
# ══════════════════════════════════════════════════════════════════════════════


class TestWebSocketConnectionStats:
    def _make_stats(self) -> WebSocketConnectionStats:
        return WebSocketConnectionStats(
            connection_id="conn-1",
            client_address="127.0.0.1:9000",
            connected_at=time.time(),
        )

    def test_compression_ratio_no_data(self):
        stats = self._make_stats()
        assert stats.compression_ratio == 1.0

    def test_compression_ratio_with_data(self):
        stats = self._make_stats()
        stats.bytes_sent_compressed = 50
        stats.bytes_sent_uncompressed = 100
        assert stats.compression_ratio == pytest.approx(0.5)

    def test_duration_seconds_active(self):
        stats = self._make_stats()
        time.sleep(0.05)
        assert stats.duration_seconds >= 0.04

    def test_duration_seconds_after_disconnect(self):
        t0 = time.time()
        stats = WebSocketConnectionStats(
            connection_id="c",
            client_address="x",
            connected_at=t0,
            disconnected_at=t0 + 5.0,
        )
        assert stats.duration_seconds == pytest.approx(5.0, abs=0.01)

    def test_to_dict_keys(self):
        stats = self._make_stats()
        d = stats.to_dict()
        expected_keys = {
            "connection_id",
            "client_address",
            "connected_at",
            "disconnected_at",
            "close_code",
            "messages_received",
            "chunks_sent",
            "bytes_sent",
            "bytes_uncompressed",
            "compression_ratio",
            "upstream_errors",
            "duration_seconds",
        }
        assert expected_keys.issubset(d.keys())


# ══════════════════════════════════════════════════════════════════════════════
# 3. WebSocketConnectionManager
# ══════════════════════════════════════════════════════════════════════════════


class TestWebSocketConnectionManager:
    def _manager(self, max_connections: int = 5) -> WebSocketConnectionManager:
        return WebSocketConnectionManager(max_connections=max_connections)

    def test_register_increments_active_count(self):
        mgr = self._manager()
        assert mgr.active_count() == 0
        mgr.register("c1", "127.0.0.1:1")
        assert mgr.active_count() == 1

    def test_can_accept_within_limit(self):
        mgr = self._manager(max_connections=2)
        assert mgr.can_accept()
        mgr.register("c1", "127.0.0.1:1")
        assert mgr.can_accept()
        mgr.register("c2", "127.0.0.1:2")
        assert not mgr.can_accept()

    def test_register_rejects_at_limit(self):
        mgr = self._manager(max_connections=1)
        assert mgr.register("c1", "addr1") is True
        assert mgr.register("c2", "addr2") is False

    def test_unregister_frees_slot(self):
        mgr = self._manager(max_connections=1)
        mgr.register("c1", "addr1")
        assert not mgr.can_accept()
        mgr.unregister("c1", close_code=1000)
        assert mgr.can_accept()

    def test_unregister_records_close_code(self):
        mgr = self._manager()
        mgr.register("c1", "addr1")
        mgr.unregister("c1", close_code=1001)
        stats = mgr.get_stats("c1")
        assert stats is not None
        assert stats.close_code == 1001
        assert stats.disconnected_at is not None

    def test_record_message_increments_counter(self):
        mgr = self._manager()
        mgr.register("c1", "addr1")
        mgr.record_message("c1")
        mgr.record_message("c1")
        assert mgr.get_stats("c1").messages_received == 2

    def test_record_chunk_accumulates(self):
        mgr = self._manager()
        mgr.register("c1", "addr1")
        mgr.record_chunk("c1", compressed=50, uncompressed=100)
        mgr.record_chunk("c1", compressed=30, uncompressed=60)
        stats = mgr.get_stats("c1")
        assert stats.chunks_sent == 2
        assert stats.bytes_sent_compressed == 80
        assert stats.bytes_sent_uncompressed == 160

    def test_record_upstream_error(self):
        mgr = self._manager()
        mgr.register("c1", "addr1")
        mgr.record_upstream_error("c1")
        assert mgr.get_stats("c1").upstream_errors == 1

    def test_get_all_stats_includes_closed(self):
        mgr = self._manager()
        mgr.register("c1", "addr1")
        mgr.register("c2", "addr2")
        mgr.unregister("c1")
        all_stats = mgr.get_all_stats()
        ids = {s["connection_id"] for s in all_stats}
        assert "c1" in ids
        assert "c2" in ids

    def test_stats_unknown_connection_returns_none(self):
        mgr = self._manager()
        assert mgr.get_stats("nonexistent") is None


# ══════════════════════════════════════════════════════════════════════════════
# 4. LRUCache
# ══════════════════════════════════════════════════════════════════════════════


class TestLRUCache:
    def test_set_and_get(self):
        cache = LRUCache(max_size_mb=1, ttl_seconds=None)
        cache.set("k1", {"data": 42})
        assert cache.get("k1") == {"data": 42}

    def test_get_missing_returns_none(self):
        cache = LRUCache()
        assert cache.get("missing") is None

    def test_miss_increments_metric(self):
        cache = LRUCache()
        cache.get("nope")
        assert cache.metrics.misses == 1

    def test_hit_increments_metric(self):
        cache = LRUCache()
        cache.set("k", "v")
        cache.get("k")
        assert cache.metrics.hits == 1

    def test_delete_existing_key(self):
        cache = LRUCache()
        cache.set("k", "v")
        assert cache.delete("k") is True
        assert cache.get("k") is None

    def test_delete_missing_returns_false(self):
        cache = LRUCache()
        assert cache.delete("ghost") is False

    def test_clear_empties_cache(self):
        cache = LRUCache()
        cache.set("a", 1)
        cache.set("b", 2)
        cache.clear()
        assert len(cache) == 0

    def test_lru_eviction_when_full(self):
        # Each int is ~28 bytes; use a tiny cache to force eviction
        cache = LRUCache(max_size_mb=0.0001, ttl_seconds=None)
        cache.set("k1", "x" * 50)
        cache.set("k2", "x" * 50)
        # k1 should have been evicted (LRU)
        assert cache.metrics.evictions_lru >= 1

    def test_ttl_expiry(self):
        cache = LRUCache(max_size_mb=1, ttl_seconds=0.01)
        cache.set("k", "value")
        time.sleep(0.05)
        assert cache.get("k") is None
        assert cache.metrics.evictions_ttl >= 1

    def test_evict_expired_removes_entries(self):
        cache = LRUCache(max_size_mb=1, ttl_seconds=0.01)
        cache.set("a", 1)
        cache.set("b", 2)
        time.sleep(0.05)
        evicted = cache.evict_expired()
        assert evicted == 2

    def test_update_existing_key(self):
        cache = LRUCache()
        cache.set("k", "v1")
        cache.set("k", "v2")
        assert cache.get("k") == "v2"
        assert len(cache) == 1

    def test_metrics_dict_structure(self):
        cache = LRUCache()
        d = cache.metrics_dict()
        for key in ("hits", "misses", "evictions_lru", "evictions_ttl", "hit_rate"):
            assert key in d

    def test_len_tracks_entries(self):
        cache = LRUCache()
        assert len(cache) == 0
        cache.set("a", 1)
        cache.set("b", 2)
        assert len(cache) == 2


# ══════════════════════════════════════════════════════════════════════════════
# 5. StatsCollector
# ══════════════════════════════════════════════════════════════════════════════


class TestStatsCollector:
    def _fresh(self) -> StatsCollector:
        return StatsCollector()

    def test_initial_snapshot_structure(self):
        sc = self._fresh()
        snap = sc.snapshot()
        assert "uptime_seconds" in snap
        assert "requests_total" in snap
        assert "compression" in snap
        assert "routing" in snap
        assert "errors" in snap
        assert "vault_search" in snap

    def test_record_request_increments_total(self):
        sc = self._fresh()
        sc.record_request(
            model="anthropic/claude", tokens_in=100, tokens_out=50, compressed=True, latency_ms=120
        )
        assert sc.snapshot()["requests_total"] == 1

    def test_compression_ratio_calculated(self):
        # tokens_in=100 sent, tokens_saved=100 → tokens_before=200, ratio=0.5
        sc = self._fresh()
        sc.record_request(
            model="claude",
            tokens_in=100,
            tokens_out=50,
            compressed=True,
            tokens_saved=100,
            latency_ms=50,
        )
        snap = sc.snapshot()
        # ratio = tokens_after / tokens_before = 100 / 200 = 0.5
        assert snap["compression"]["ratio"] == pytest.approx(0.5)

    def test_uncompressed_request_skipped_counter(self):
        sc = self._fresh()
        sc.record_request(
            model="claude", tokens_in=100, tokens_out=100, compressed=False, latency_ms=30
        )
        assert sc.snapshot()["compression"]["skipped"] == 1

    def test_routing_breakdown(self):
        sc = self._fresh()
        sc.record_request(
            model="anthropic/claude-3", tokens_in=10, tokens_out=5, compressed=True, latency_ms=20
        )
        routing = sc.snapshot()["routing"]
        assert "anthropic_claude" in routing
        assert routing["anthropic_claude"] == 1

    def test_model_normalisation_google(self):
        sc = self._fresh()
        sc.record_request(
            model="google/gemini-pro", tokens_in=10, tokens_out=5, compressed=False, latency_ms=15
        )
        routing = sc.snapshot()["routing"]
        assert "google_gemini" in routing

    def test_model_normalisation_openai(self):
        sc = self._fresh()
        sc.record_request(
            model="gpt-4o", tokens_in=10, tokens_out=5, compressed=False, latency_ms=15
        )
        routing = sc.snapshot()["routing"]
        assert "openai" in routing

    def test_error_recording(self):
        sc = self._fresh()
        sc.record_error("AUTH_001")
        sc.record_error("AUTH_001")
        sc.record_error("RATE_429")
        snap = sc.snapshot()
        assert snap["errors"]["AUTH_001"] == 2
        assert snap["errors"]["RATE_429"] == 1
        assert snap["errors"]["total"] == 3

    def test_vault_search_hit_rate(self):
        sc = self._fresh()
        sc.record_vault_search(hit=True)
        sc.record_vault_search(hit=True)
        sc.record_vault_search(hit=False)
        vs = sc.snapshot()["vault_search"]
        assert vs["cache_hits"] == 2
        assert vs["cache_misses"] == 1
        assert vs["hit_rate"] == pytest.approx(2 / 3, rel=1e-3)

    def test_latest_request_latency(self):
        sc = self._fresh()
        sc.record_request(
            model="claude", tokens_in=10, tokens_out=5, compressed=True, latency_ms=99.5
        )
        assert sc.snapshot()["latest_request_ms"] == pytest.approx(99.5)

    def test_to_text_returns_string(self):
        sc = self._fresh()
        sc.record_request(
            model="claude", tokens_in=100, tokens_out=50, compressed=True, latency_ms=55
        )
        text = sc.to_text()
        assert "requests_total=1" in text
        assert "uptime_seconds=" in text
