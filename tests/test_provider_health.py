#!/usr/bin/env python3
"""
Test suite for provider health monitoring.
"""

import pytest

# provider_health is a standalone external package, not bundled with tokenpak
# OSS or any of its extras. Skip cleanly so the release test gate stays green.
pytest.importorskip("provider_health", reason="provider_health is a separate external package not installed in slim test env")

import time
from provider_health import (
    ProviderHealthMonitor,
    ProviderMetrics,
    get_monitor,
    record_provider_request,
)


class TestProviderMetrics:
    """Test basic metrics calculation."""

    def test_initial_metrics_empty(self):
        """Fresh metrics should be empty."""
        m = ProviderMetrics(provider="test", latencies_ms=[])
        assert m.request_count == 0
        assert m.error_count == 0
        assert m.success_count == 0
        assert m.p50_latency == 0.0
        assert m.p99_latency == 0.0

    def test_to_dict_format(self):
        """to_dict() should return JSON-friendly dict."""
        m = ProviderMetrics(
            provider="anthropic",
            latencies_ms=[100, 150, 200],
            request_count=3,
            success_count=3,
            error_count=0,
            status="GREEN",
            p50_latency=150.0,
            p99_latency=200.0,
            success_rate=1.0,
            error_rate=0.0,
            last_seen="2026-03-25T19:30:00Z",
        )
        d = m.to_dict()
        assert d["provider"] == "anthropic"
        assert d["request_count"] == 3
        assert d["success_count"] == 3
        assert d["error_count"] == 0
        assert d["status"] == "GREEN"
        assert d["p50_latency_ms"] == 150.0
        assert d["p99_latency_ms"] == 200.0
        assert d["success_rate"] == 100.0
        assert d["error_rate"] == 0.0
        assert "latencies_ms" not in d  # Should not include raw latencies list


class TestProviderHealthMonitor:
    """Test the monitor class."""

    @pytest.fixture
    def monitor(self):
        """Fresh monitor for each test."""
        m = ProviderHealthMonitor()
        yield m
        m.clear()

    def test_record_single_success(self, monitor):
        """Record a single successful request."""
        monitor.record_request("anthropic", latency_ms=100.0, status_code=200)

        health = monitor.get_provider_health("anthropic")
        assert health is not None
        assert health["provider"] == "anthropic"
        assert health["request_count"] == 1
        assert health["success_count"] == 1
        assert health["error_count"] == 0
        assert health["success_rate"] == 100.0
        assert health["error_rate"] == 0.0
        assert health["status"] == "GREEN"

    def test_record_single_error(self, monitor):
        """Record a single error request."""
        monitor.record_request("openai", latency_ms=50.0, status_code=500)

        health = monitor.get_provider_health("openai")
        assert health is not None
        assert health["request_count"] == 1
        assert health["error_count"] == 1
        assert health["success_count"] == 0
        assert health["error_rate"] == 100.0
        assert health["success_rate"] == 0.0
        assert health["status"] == "RED"

    def test_mixed_requests(self, monitor):
        """Record a mix of success and errors."""
        # 95% success (19/20)
        for _ in range(19):
            monitor.record_request("google", latency_ms=150.0, status_code=200)
        for _ in range(1):
            monitor.record_request("google", latency_ms=50.0, status_code=503)

        health = monitor.get_provider_health("google")
        assert health["request_count"] == 20
        assert health["success_count"] == 19
        assert health["error_count"] == 1
        assert health["success_rate"] == 95.0
        assert health["error_rate"] == 5.0
        assert health["status"] in ["YELLOW", "RED"]  # Could be RED if p99 is high enough

    def test_latency_percentiles(self, monitor):
        """Test p50 and p99 calculation."""
        latencies = [100, 150, 200, 250, 300, 350, 400, 450, 500, 550]
        for lat in latencies:
            monitor.record_request("test", latency_ms=float(lat), status_code=200)

        health = monitor.get_provider_health("test")
        # p50 should be median: (300 + 350) / 2 = 325 for 10 items
        assert health["p50_latency_ms"] == 325.0
        # p99 should be near the end
        assert health["p99_latency_ms"] >= 450.0

    def test_multiple_providers(self, monitor):
        """Monitor multiple providers independently."""
        monitor.record_request("anthropic", latency_ms=100.0, status_code=200)
        monitor.record_request("anthropic", latency_ms=150.0, status_code=200)
        monitor.record_request("openai", latency_ms=200.0, status_code=500)

        all_health = monitor.get_all_health()
        assert all_health["total_providers"] == 2
        assert "anthropic" in all_health["providers"]
        assert "openai" in all_health["providers"]
        assert all_health["providers"]["anthropic"]["success_rate"] == 100.0
        assert all_health["providers"]["openai"]["error_rate"] == 100.0

    def test_status_green(self, monitor):
        """Status GREEN when >99% success and p99 < 2s."""
        # Record 100 successful requests with low latency
        for _ in range(100):
            monitor.record_request("test", latency_ms=500.0, status_code=200)

        health = monitor.get_provider_health("test")
        assert health["status"] == "GREEN"
        assert health["success_rate"] == 100.0

    def test_status_red(self, monitor):
        """Status RED when <95% success."""
        # 90% success rate
        for _ in range(9):
            monitor.record_request("test", latency_ms=100.0, status_code=200)
        for _ in range(1):
            monitor.record_request("test", latency_ms=100.0, status_code=500)

        health = monitor.get_provider_health("test")
        assert health["status"] == "RED"
        assert health["success_rate"] == 90.0

    def test_thread_safety(self, monitor):
        """Multiple threads recording simultaneously."""
        import threading

        def worker(provider_id):
            for i in range(100):
                status = 200 if i % 10 != 0 else 500
                monitor.record_request(f"provider_{provider_id}", float(i), status)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        all_health = monitor.get_all_health()
        assert all_health["total_providers"] == 5
        for provider_id in range(5):
            health = all_health["providers"][f"provider_{provider_id}"]
            assert health["request_count"] == 100

    def test_last_seen_updated(self, monitor):
        """last_seen timestamp should update on each request."""
        monitor.record_request("test", 100.0, 200)
        health1 = monitor.get_provider_health("test")
        first_timestamp = health1["last_seen"]

        time.sleep(0.1)
        monitor.record_request("test", 100.0, 200)
        health2 = monitor.get_provider_health("test")
        second_timestamp = health2["last_seen"]

        assert second_timestamp > first_timestamp

    def test_get_nonexistent_provider(self, monitor):
        """Getting nonexistent provider should return None."""
        health = monitor.get_provider_health("nonexistent")
        assert health is None

    def test_clear(self, monitor):
        """clear() should reset all metrics."""
        monitor.record_request("test", 100.0, 200)
        assert len(monitor.metrics) > 0

        monitor.clear()
        assert len(monitor.metrics) == 0


class TestGlobalSingleton:
    """Test global singleton functions."""

    def test_get_monitor_singleton(self):
        """get_monitor() should return same instance."""
        m1 = get_monitor()
        m2 = get_monitor()
        assert m1 is m2

    def test_record_via_convenience_function(self):
        """record_provider_request() should use global singleton."""
        monitor = get_monitor()
        monitor.clear()

        record_provider_request("test", 100.0, 200)
        health = monitor.get_provider_health("test")
        assert health is not None
        assert health["request_count"] == 1


class TestEdgeCases:
    """Test edge cases and error conditions."""

    @pytest.fixture
    def monitor(self):
        m = ProviderHealthMonitor()
        yield m
        m.clear()

    def test_zero_latency(self, monitor):
        """Handle zero latency (should not crash)."""
        monitor.record_request("test", 0.0, 200)
        health = monitor.get_provider_health("test")
        assert health["p50_latency_ms"] == 0.0

    def test_very_high_latency(self, monitor):
        """Handle very high latency."""
        monitor.record_request("test", 99999.0, 200)
        health = monitor.get_provider_health("test")
        assert health["p50_latency_ms"] == 99999.0

    def test_non_2xx_non_5xx_status(self, monitor):
        """3xx, 4xx (non-5xx errors) should not count as errors."""
        monitor.record_request("test", 100.0, 301)  # Redirect
        monitor.record_request("test", 100.0, 400)  # Bad request
        monitor.record_request("test", 100.0, 404)  # Not found
        monitor.record_request("test", 100.0, 200)  # Success

        health = monitor.get_provider_health("test")
        # Only the 200 counts as success
        assert health["success_count"] == 1
        assert health["error_count"] == 0  # 3xx/4xx don't count as errors
        assert health["request_count"] == 4

    def test_max_latencies_buffer(self, monitor):
        """Buffer should cap at MAX_LATENCIES_PER_PROVIDER."""
        # Record many requests to exceed buffer
        for i in range(2000):
            monitor.record_request("test", float(i), 200)

        health = monitor.get_provider_health("test")
        assert health["request_count"] == 2000
        # Buffer keeps only last 1000, but metrics should still be correct
        m = monitor.metrics["test"]
        assert len(m.latencies_ms) <= monitor.MAX_LATENCIES_PER_PROVIDER


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
