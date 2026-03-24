"""
Tests for tokenpak.proxy.stats.StatsCollector
"""
import time
import threading
import pytest

from tokenpak.proxy.stats import StatsCollector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fresh() -> StatsCollector:
    return StatsCollector()


# ---------------------------------------------------------------------------
# Test 1: Fresh collector returns zero/default values
# ---------------------------------------------------------------------------

def test_fresh_snapshot_defaults():
    sc = fresh()
    s = sc.snapshot()

    assert s["requests_total"] == 0
    assert s["requests_per_sec"] >= 0
    assert s["compression"]["tokens_before"] == 0
    assert s["compression"]["tokens_after"] == 0
    assert s["compression"]["compressed"] == 0
    assert s["compression"]["skipped"] == 0
    assert s["compression"]["ratio"] == 0.0
    assert s["routing"] == {}
    assert s["errors"]["total"] == 0
    assert s["vault_search"]["cache_hits"] == 0
    assert s["vault_search"]["cache_misses"] == 0
    assert s["vault_search"]["hit_rate"] == 0.0
    assert s["latest_request_ms"] is None
    assert "timestamp" in s
    assert s["uptime_seconds"] >= 0


# ---------------------------------------------------------------------------
# Test 2: record_request tracks counters correctly
# ---------------------------------------------------------------------------

def test_record_request_counters():
    sc = fresh()
    sc.record_request(model="anthropic/claude-3-5-sonnet",
                      tokens_in=600, tokens_saved=400,
                      compressed=True, latency_ms=120.0)
    s = sc.snapshot()

    assert s["requests_total"] == 1
    assert s["compression"]["tokens_before"] == 1000  # 600 + 400
    assert s["compression"]["tokens_after"] == 600
    assert s["compression"]["compressed"] == 1
    assert s["compression"]["skipped"] == 0
    assert s["compression"]["ratio"] == pytest.approx(0.6, abs=0.001)
    assert s["latest_request_ms"] == 120.0


# ---------------------------------------------------------------------------
# Test 3: Model routing normalisation
# ---------------------------------------------------------------------------

def test_routing_normalisation():
    sc = fresh()
    sc.record_request(model="anthropic/claude-3-haiku-20240307")
    sc.record_request(model="gemini-1.5-flash")
    sc.record_request(model="gpt-4o")
    sc.record_request(model="llama3.2")

    s = sc.snapshot()
    routing = s["routing"]

    assert routing.get("anthropic_claude", 0) == 1
    assert routing.get("google_gemini", 0) == 1
    assert routing.get("openai", 0) == 1
    assert routing.get("ollama", 0) == 1


# ---------------------------------------------------------------------------
# Test 4: Error recording and total
# ---------------------------------------------------------------------------

def test_error_recording():
    sc = fresh()
    sc.record_error("AUTH_001")
    sc.record_error("AUTH_001")
    sc.record_error("RATE_LIMIT_001")

    s = sc.snapshot()
    assert s["errors"]["AUTH_001"] == 2
    assert s["errors"]["RATE_LIMIT_001"] == 1
    assert s["errors"]["total"] == 3


# ---------------------------------------------------------------------------
# Test 5: Vault search hit rate
# ---------------------------------------------------------------------------

def test_vault_search_hit_rate():
    sc = fresh()
    for _ in range(8):
        sc.record_vault_search(hit=True)
    for _ in range(2):
        sc.record_vault_search(hit=False)

    s = sc.snapshot()
    vs = s["vault_search"]
    assert vs["cache_hits"] == 8
    assert vs["cache_misses"] == 2
    assert vs["hit_rate"] == pytest.approx(0.8, abs=0.001)


# ---------------------------------------------------------------------------
# Test 6: reset() clears all counters
# ---------------------------------------------------------------------------

def test_reset():
    sc = fresh()
    sc.record_request(model="claude", tokens_in=500, tokens_saved=200, compressed=True)
    sc.record_error("PROXY_001")
    sc.record_vault_search(hit=True)

    sc.reset()
    s = sc.snapshot()

    assert s["requests_total"] == 0
    assert s["errors"]["total"] == 0
    assert s["vault_search"]["cache_hits"] == 0
    assert s["compression"]["tokens_before"] == 0
    assert s["latest_request_ms"] is None


# ---------------------------------------------------------------------------
# Test 7: Thread safety — concurrent writes don't corrupt counters
# ---------------------------------------------------------------------------

def test_thread_safety():
    sc = fresh()
    N = 200
    threads = []

    def worker():
        for _ in range(N):
            sc.record_request(model="claude", tokens_in=100, tokens_saved=50, compressed=True)
            sc.record_vault_search(hit=True)

    for _ in range(10):
        t = threading.Thread(target=worker)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    s = sc.snapshot()
    expected = 10 * N
    assert s["requests_total"] == expected
    assert s["vault_search"]["cache_hits"] == expected


# ---------------------------------------------------------------------------
# Test 8: requests_per_sec is computed from uptime
# ---------------------------------------------------------------------------

def test_requests_per_sec():
    sc = fresh()
    sc.record_request()
    sc.record_request()
    sc.record_request()

    s = sc.snapshot()
    # Must be positive and sensible (not NaN, not inf, not 0 for 3 requests)
    assert s["requests_per_sec"] > 0
    assert s["requests_per_sec"] < 1_000_000  # sanity upper bound


# ---------------------------------------------------------------------------
# Test 9: snapshot JSON contains all required top-level keys
# ---------------------------------------------------------------------------

def test_snapshot_schema():
    sc = fresh()
    s = sc.snapshot()
    required_keys = {
        "uptime_seconds", "requests_total", "requests_per_sec",
        "compression", "routing", "errors", "vault_search",
        "latest_request_ms", "timestamp",
    }
    assert required_keys == required_keys & s.keys()


# ---------------------------------------------------------------------------
# Test 10: Skipped (uncompressed) requests are tracked separately
# ---------------------------------------------------------------------------

def test_skipped_tracking():
    sc = fresh()
    sc.record_request(model="claude", tokens_in=400, tokens_saved=0, compressed=False)
    sc.record_request(model="claude", tokens_in=400, tokens_saved=0, compressed=False)
    sc.record_request(model="claude", tokens_in=300, tokens_saved=150, compressed=True)

    s = sc.snapshot()
    assert s["compression"]["compressed"] == 1
    assert s["compression"]["skipped"] == 2
    assert s["requests_total"] == 3
