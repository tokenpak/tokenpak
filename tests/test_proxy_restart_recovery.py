#!/usr/bin/env python3
"""
Tests for TokenPak proxy state across restarts.

History: this file previously held 16 tests; 15 were vacuous — they asserted
on local variables, literals, or MagicMock objects they had just configured
(e.g. ``proxy_started = True; assert proxy_started``), exercising no product
code at all. Those were removed; only the test below actually imports and
exercises TokenPak behavior. Real shutdown/restart behavior is covered by:
  - tests/test_async_proxy_server.py::test_async_proxy_start_stop_cycle
  - tests/chaos/test_chaos.py::TestProxyLifecycle (stop idempotency,
    non-blocking start, port-conflict startup failure)
  - tests/proxy/test_crash_durability.py (SIGKILL + WAL recovery)
"""

import pytest


class TestStatsResetOnRestart:
    """Metrics/stats behavior across proxy restarts."""

    def test_stats_clear_on_new_instance(self):
        """A fresh ProxyStats instance (as created on restart) starts zeroed."""
        from tokenpak.proxy import ProxyStats

        # First instance accumulates some counters
        stats1 = ProxyStats()
        stats1.requests_total = 100
        stats1.tokens_processed = 50000
        stats1.errors_total = 5

        assert stats1.requests_total == 100

        # New instance (simulating restart) must not inherit state
        stats2 = ProxyStats()
        assert stats2.requests_total == 0
        assert stats2.tokens_processed == 0
        assert stats2.errors_total == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
