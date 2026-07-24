# SPDX-License-Identifier: Apache-2.0
"""
tests/test_ttl_attribution.py

Tests for Anthropic prompt-cache TTL attribution telemetry.

Covers:
- ``parse_ttl_attribution`` — parses ``usage.cache_creation.ephemeral_*_input_tokens``
  into ``(1h_tokens, 5m_tokens, attribution-label)``.
- ``CacheMetrics`` carries the new fields; the collector aggregates per-TTL
  totals and a per-attribution histogram.
- ``extract_sse_tokens`` additively returns the new keys.
- ``monitor.Monitor`` schema migration is backward-compatible (old DB without
  the new columns is upgraded; old call sites without new kwargs still work).
- ``status``-shape summary surfaces the new fields when populated.

All telemetry; no byte-preserved request mutation anywhere in this suite.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tokenpak.cache.telemetry import (
    CacheMetrics,
    CacheTelemetryCollector,
    parse_ttl_attribution,
)
from tokenpak.proxy.streaming import extract_sse_tokens

# ---------------------------------------------------------------------------
# parse_ttl_attribution
# ---------------------------------------------------------------------------


def _usage(flat: int = 0, one_h: int = 0, five_m: int = 0, with_breakdown: bool = True) -> dict:
    u: dict = {"cache_creation_input_tokens": flat}
    if with_breakdown and (one_h or five_m):
        u["cache_creation"] = {
            "ephemeral_1h_input_tokens": one_h,
            "ephemeral_5m_input_tokens": five_m,
        }
    return u


def test_attribution_1h_only():
    r = parse_ttl_attribution(_usage(flat=400, one_h=400))
    assert r == {
        "ephemeral_1h_tokens": 400,
        "ephemeral_5m_tokens": 0,
        "ttl_attribution": "1h",
    }


def test_attribution_5m_only():
    r = parse_ttl_attribution(_usage(flat=128, five_m=128))
    assert r["ttl_attribution"] == "5m"
    assert r["ephemeral_5m_tokens"] == 128 and r["ephemeral_1h_tokens"] == 0


def test_attribution_mixed():
    r = parse_ttl_attribution(_usage(flat=600, one_h=200, five_m=400))
    assert r["ttl_attribution"] == "mixed"
    assert r["ephemeral_1h_tokens"] == 200 and r["ephemeral_5m_tokens"] == 400


def test_attribution_unknown_when_flat_but_no_breakdown():
    """Cache creation happened but the response omitted the per-TTL split."""
    r = parse_ttl_attribution(_usage(flat=512, with_breakdown=False))
    assert r["ttl_attribution"] == "unknown"
    assert r["ephemeral_1h_tokens"] == 0 and r["ephemeral_5m_tokens"] == 0


def test_attribution_none_when_no_creation():
    r = parse_ttl_attribution({"cache_creation_input_tokens": 0})
    assert r["ttl_attribution"] == "none"


def test_attribution_fail_open_on_malformed():
    # Wrong type for cache_creation; should fail-open to "unknown" without raising.
    r = parse_ttl_attribution({"cache_creation_input_tokens": 10, "cache_creation": "oops"})
    assert r["ttl_attribution"] == "unknown"

    r2 = parse_ttl_attribution("not a dict")  # type: ignore[arg-type]
    assert r2["ttl_attribution"] == "unknown"
    assert r2["ephemeral_1h_tokens"] == 0


# ---------------------------------------------------------------------------
# CacheMetrics + collector aggregation
# ---------------------------------------------------------------------------


def _metrics(
    *, ttl: str | None, one_h: int = 0, five_m: int = 0, cached: bool = True
) -> CacheMetrics:
    return CacheMetrics(
        request_id="r",
        stable_prefix_tokens=1000,
        stable_cached=cached,
        cache_read_tokens=900 if cached else 0,
        total_input_tokens=1000,
        cache_creation_tokens=one_h + five_m,
        cache_creation_ephemeral_1h_tokens=one_h,
        cache_creation_ephemeral_5m_tokens=five_m,
        ttl_attribution=ttl,
        output_tokens=10,
    )


def test_collector_aggregates_per_ttl_totals_and_counts():
    c = CacheTelemetryCollector()
    c.record(_metrics(ttl="1h", one_h=400))
    c.record(_metrics(ttl="5m", five_m=128))
    c.record(_metrics(ttl="mixed", one_h=200, five_m=300))
    c.record(_metrics(ttl="none", cached=True))  # no creation
    c.record(_metrics(ttl=None, one_h=999, five_m=999))  # legacy: token sums still add

    summary = c.summary()
    assert summary["total_cache_creation_ephemeral_1h_tokens"] == 400 + 200 + 999
    assert summary["total_cache_creation_ephemeral_5m_tokens"] == 128 + 300 + 999
    counts = summary["ttl_attribution_counts"]
    assert counts == {"1h": 1, "5m": 1, "mixed": 1, "none": 1}  # None entry skipped
    # Also: by_ttl_attribution mirror
    assert c.by_ttl_attribution() == counts


def test_to_dict_includes_new_fields():
    m = _metrics(ttl="1h", one_h=500)
    d = m.to_dict()
    assert d["cache_creation_ephemeral_1h_tokens"] == 500
    assert d["cache_creation_ephemeral_5m_tokens"] == 0
    assert d["ttl_attribution"] == "1h"


def test_clear_resets_ttl_state():
    c = CacheTelemetryCollector()
    c.record(_metrics(ttl="1h", one_h=400))
    assert c.by_ttl_attribution() == {"1h": 1}
    c.clear()
    assert c.by_ttl_attribution() == {}
    assert c.summary()["total_cache_creation_ephemeral_1h_tokens"] == 0


# ---------------------------------------------------------------------------
# SSE extractor (additive)
# ---------------------------------------------------------------------------


def _sse(message_usage: dict) -> bytes:
    event = {"type": "message_start", "message": {"usage": message_usage}}
    return f"data: {json.dumps(event)}\n\n".encode()


def test_sse_extracts_per_ttl_breakdown():
    sse = _sse(
        {
            "cache_read_input_tokens": 1500,
            "cache_creation_input_tokens": 600,
            "cache_creation": {
                "ephemeral_1h_input_tokens": 400,
                "ephemeral_5m_input_tokens": 200,
            },
        }
    )
    r = extract_sse_tokens(sse)
    assert r["cache_creation_input_tokens"] == 600
    assert r["cache_creation_ephemeral_1h_input_tokens"] == 400
    assert r["cache_creation_ephemeral_5m_input_tokens"] == 200


def test_sse_legacy_payload_still_parses_flat_only():
    """Old payload without per-TTL breakdown → new keys default to 0."""
    sse = _sse(
        {
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 50,
        }
    )
    r = extract_sse_tokens(sse)
    assert r["cache_creation_input_tokens"] == 50
    assert r["cache_creation_ephemeral_1h_input_tokens"] == 0
    assert r["cache_creation_ephemeral_5m_input_tokens"] == 0


# ---------------------------------------------------------------------------
# monitor.Monitor schema migration + log() backward-compat
# ---------------------------------------------------------------------------


def _old_schema_db(path: Path) -> None:
    """Create a requests table at the v3-ish schema (no new TTL columns)."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        """CREATE TABLE requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            model TEXT NOT NULL,
            request_type TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            estimated_cost REAL,
            latency_ms INTEGER,
            status_code INTEGER,
            endpoint TEXT,
            compilation_mode TEXT,
            protected_tokens INTEGER,
            compressed_tokens INTEGER,
            injected_tokens INTEGER DEFAULT 0,
            injected_sources TEXT DEFAULT '',
            cache_read_tokens INTEGER DEFAULT 0,
            cache_creation_tokens INTEGER DEFAULT 0,
            would_have_saved INTEGER DEFAULT 0,
            user_id TEXT DEFAULT ''
        )"""
    )
    conn.commit()
    conn.close()


def _columns(db: Path) -> set[str]:
    conn = sqlite3.connect(str(db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(requests)")}
    conn.close()
    return cols


def test_monitor_migrates_old_db_additively(tmp_path: Path):
    db = tmp_path / "monitor.db"
    _old_schema_db(db)
    pre_cols = _columns(db)
    assert "ttl_attribution" not in pre_cols
    assert "cache_creation_ephemeral_1h_tokens" not in pre_cols

    from tokenpak.proxy.monitor import Monitor

    Monitor(db_path=db)  # triggers _init_db migration
    post_cols = _columns(db)
    # Pre-existing columns untouched, new columns added.
    assert pre_cols.issubset(post_cols)
    for new_col in (
        "cache_origin",
        "cache_creation_ephemeral_1h_tokens",
        "cache_creation_ephemeral_5m_tokens",
        "ttl_attribution",
    ):
        assert new_col in post_cols, f"missing migrated column: {new_col}"


def test_monitor_log_without_new_kwargs_still_works(tmp_path: Path):
    """Existing call sites that don't pass TTL kwargs must not break."""
    from tokenpak.proxy.monitor import Monitor

    m = Monitor(db_path=tmp_path / "monitor.db")
    # No TTL kwargs at all — backward-compatible signature.
    m.log(
        model="claude-sonnet",
        input_tokens=100,
        output_tokens=10,
        cost=0.001,
        latency_ms=500,
        status_code=200,
        endpoint="/v1/messages",
    )


def test_monitor_log_with_ttl_kwargs_persists(tmp_path: Path):
    from tokenpak.proxy.monitor import Monitor

    db = tmp_path / "monitor.db"
    m = Monitor(db_path=db)
    m.log(
        model="claude-sonnet",
        input_tokens=2000,
        output_tokens=50,
        cost=0.002,
        latency_ms=600,
        status_code=200,
        endpoint="/v1/messages",
        cache_creation_tokens=600,
        cache_creation_ephemeral_1h_tokens=400,
        cache_creation_ephemeral_5m_tokens=200,
        ttl_attribution="mixed",
    )
    # Flush the async write queue by writing through the synchronous fallback
    # path — call sites use the queue, tests can drain by waiting.
    import time

    for _ in range(40):
        time.sleep(0.05)
        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            "SELECT cache_creation_ephemeral_1h_tokens, cache_creation_ephemeral_5m_tokens, ttl_attribution "
            "FROM requests"
        ).fetchall()
        conn.close()
        if rows:
            break
    assert rows, "row was not written to monitor.db"
    assert rows[-1] == (400, 200, "mixed")
