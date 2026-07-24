"""
tests/test_cache_telemetry.py

Unit tests for tokenpak.cache.telemetry — CacheMetrics and
CacheTelemetryCollector.
"""

from __future__ import annotations

import time

from tokenpak.cache.telemetry import (
    CacheMetrics,
    CacheTelemetryCollector,
    get_collector,
    reset_collector,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hit(
    request_id: str = "req_hit", cache_read: int = 13_500, total: int = 15_000, output: int = 512
) -> CacheMetrics:
    return CacheMetrics(
        request_id=request_id,
        stable_prefix_tokens=14_800,
        stable_cached=True,
        cache_read_tokens=cache_read,
        total_input_tokens=total,
        output_tokens=output,
    )


def _miss(
    request_id: str = "req_miss", reason: str = "timestamp", total: int = 15_000
) -> CacheMetrics:
    return CacheMetrics(
        request_id=request_id,
        stable_prefix_tokens=14_800,
        stable_cached=False,
        cache_miss_reason=reason,
        cache_read_tokens=0,
        total_input_tokens=total,
    )


# ---------------------------------------------------------------------------
# CacheMetrics unit tests
# ---------------------------------------------------------------------------


class TestCacheMetrics:
    def test_cache_hit_true_when_read_tokens_positive(self):
        m = _hit(cache_read=1)
        assert m.cache_hit is True

    def test_cache_hit_false_when_read_tokens_zero(self):
        m = _miss()
        assert m.cache_hit is False

    def test_cache_hit_ratio_correct(self):
        m = _hit(cache_read=13_500, total=15_000)
        assert abs(m.cache_hit_ratio - 13_500 / 15_000) < 1e-9

    def test_cache_hit_ratio_zero_when_no_input_tokens(self):
        m = CacheMetrics(
            request_id="empty",
            stable_prefix_tokens=0,
            stable_cached=False,
            total_input_tokens=0,
        )
        assert m.cache_hit_ratio == 0.0

    def test_cost_saved_proportional_to_cache_read(self):
        m = _hit(cache_read=10_000)
        # 90% saving factor
        assert abs(m.cost_saved - 10_000 * 0.90) < 1e-6

    def test_cost_saved_zero_on_miss(self):
        m = _miss()
        assert m.cost_saved == 0.0

    def test_to_dict_contains_required_fields(self):
        m = _hit()
        d = m.to_dict()
        for key in (
            "request_id",
            "cache_hit",
            "cache_hit_ratio",
            "cache_miss_reason",
            "total_input_tokens",
            "cache_read_tokens",
            "cost_saved",
        ):
            assert key in d, f"Missing key: {key}"

    def test_timestamp_auto_populated(self):
        before = time.time()
        m = _hit()
        after = time.time()
        assert before <= m.timestamp <= after


# ---------------------------------------------------------------------------
# CacheTelemetryCollector unit tests
# ---------------------------------------------------------------------------


class TestCacheTelemetryCollector:
    def test_hit_rate_empty_returns_zero(self):
        c = CacheTelemetryCollector()
        assert c.hit_rate() == 0.0

    def test_hit_rate_all_hits(self):
        c = CacheTelemetryCollector()
        for i in range(5):
            c.record(_hit(request_id=f"req_{i}"))
        assert c.hit_rate() == 1.0

    def test_hit_rate_all_misses(self):
        c = CacheTelemetryCollector()
        for i in range(4):
            c.record(_miss(request_id=f"miss_{i}"))
        assert c.hit_rate() == 0.0

    def test_hit_rate_mixed(self):
        """3 hits, 1 miss → 0.75"""
        c = CacheTelemetryCollector()
        for i in range(3):
            c.record(_hit(request_id=f"h_{i}"))
        c.record(_miss(request_id="m_0"))
        assert abs(c.hit_rate() - 0.75) < 1e-9

    def test_avg_cache_ratio_single_request(self):
        c = CacheTelemetryCollector()
        c.record(_hit(cache_read=13_500, total=15_000))
        expected = 13_500 / 15_000
        assert abs(c.avg_cache_ratio() - expected) < 1e-9

    def test_avg_cache_ratio_two_requests(self):
        """(90% + 80%) / 2 = 85%"""
        c = CacheTelemetryCollector()
        c.record(_hit(cache_read=13_500, total=15_000))  # 90%
        c.record(_hit(cache_read=12_000, total=15_000))  # 80%
        assert abs(c.avg_cache_ratio() - 0.85) < 1e-9

    def test_avg_cache_ratio_empty(self):
        c = CacheTelemetryCollector()
        assert c.avg_cache_ratio() == 0.0

    def test_by_miss_reason_counts(self):
        c = CacheTelemetryCollector()
        c.record(_miss(reason="timestamp"))
        c.record(_miss(reason="timestamp"))
        c.record(_miss(reason="uuid"))
        c.record(_hit())

        reasons = c.by_miss_reason()
        assert reasons["timestamp"] == 2
        assert reasons["uuid"] == 1
        assert "unknown" not in reasons  # not recorded

    def test_by_miss_reason_empty_on_no_misses(self):
        c = CacheTelemetryCollector()
        c.record(_hit())
        assert c.by_miss_reason() == {}

    def test_recent_requests_bounded(self):
        c = CacheTelemetryCollector(max_recent=5)
        for i in range(10):
            c.record(_hit(request_id=f"r_{i}"))
        # Should only keep last 5
        recent = c.recent_requests(n=10)
        assert len(recent) == 5
        assert recent[-1]["request_id"] == "r_9"

    def test_recent_requests_returns_newest_last(self):
        c = CacheTelemetryCollector()
        c.record(_hit(request_id="first"))
        c.record(_miss(request_id="second"))
        recent = c.recent_requests(n=2)
        assert recent[0]["request_id"] == "first"
        assert recent[1]["request_id"] == "second"

    def test_summary_structure(self):
        c = CacheTelemetryCollector()
        c.record(_hit())
        c.record(_miss())
        s = c.summary()

        required_keys = [
            "total_requests",
            "cache_hits",
            "cache_misses",
            "hit_rate",
            "miss_rate",
            "hit_rate_pct",
            "avg_cache_ratio",
            "avg_cache_ratio_pct",
            "total_cache_read_tokens",
            "total_input_tokens",
            "estimated_cost_saved_tokens",
            "miss_reasons",
            "recent_requests",
        ]
        for k in required_keys:
            assert k in s, f"Missing key in summary: {k}"

    def test_summary_totals_accumulate(self):
        c = CacheTelemetryCollector()
        c.record(_hit(cache_read=10_000, total=15_000, output=100))
        c.record(_hit(cache_read=12_000, total=15_000, output=200))
        s = c.summary()
        assert s["total_requests"] == 2
        assert s["cache_hits"] == 2
        assert s["total_cache_read_tokens"] == 22_000
        assert s["total_input_tokens"] == 30_000
        assert s["total_output_tokens"] == 300

    def test_thread_safety_concurrent_records(self):
        """Multiple threads recording simultaneously must not corrupt state."""
        import threading

        c = CacheTelemetryCollector()
        errors = []

        def record_many(thread_id: int):
            try:
                for i in range(50):
                    c.record(_hit(request_id=f"t{thread_id}_r{i}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=record_many, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        assert c.hit_rate() == 1.0  # all were hits
        # 4 threads × 50 = 200 total
        s = c.summary()
        assert s["total_requests"] == 200


# ---------------------------------------------------------------------------
# Module-level singleton tests
# ---------------------------------------------------------------------------


class TestModuleSingleton:
    def setup_method(self):
        reset_collector()

    def teardown_method(self):
        reset_collector()

    def test_get_collector_returns_same_instance(self):
        c1 = get_collector()
        c2 = get_collector()
        assert c1 is c2

    def test_reset_collector_creates_fresh_instance(self):
        c1 = get_collector()
        c1.record(_hit())
        reset_collector()
        c2 = get_collector()
        assert c1 is not c2
        assert c2.hit_rate() == 0.0

    def test_singleton_accumulates_across_calls(self):
        get_collector().record(_hit(request_id="a"))
        get_collector().record(_miss(request_id="b"))
        assert get_collector().summary()["total_requests"] == 2
