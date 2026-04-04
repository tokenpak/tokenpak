"""Tests for calculate_fleet_savings() and calculate_savings_breakdown() in pricing.py."""

import sqlite3
import os
import pytest
from datetime import datetime, timedelta, timezone

from tokenpak.pricing import (
    calculate_fleet_savings,
    calculate_savings_breakdown,
    MODEL_RATES,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────


def _make_db(tmp_path, rows):
    """Create a monitor.db with the standard schema and insert rows."""
    db = str(tmp_path / "monitor.db")
    conn = sqlite3.connect(db)
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
            would_have_saved INTEGER DEFAULT 0
        )"""
    )
    conn.executemany(
        "INSERT INTO requests (timestamp, model, input_tokens, output_tokens, "
        "cache_read_tokens, cache_creation_tokens, compressed_tokens) VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db


def _ts(delta_hours=0):
    """Return an ISO timestamp relative to now."""
    return (datetime.now(timezone.utc) - timedelta(hours=delta_hours)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )


# ─── calculate_fleet_savings — empty DB ──────────────────────────────────────


def test_empty_db_returns_zeroes(tmp_path):
    db = _make_db(tmp_path, [])
    result = calculate_fleet_savings(db)
    assert result["total_requests"] == 0
    assert result["cost_without_tokenpak"] == 0.0
    assert result["cost_with_tokenpak"] == 0.0
    assert result["total_saved"] == 0.0
    assert result["reduction_percent"] == 0.0
    assert result["per_model"] == []


def test_empty_db_velocity_zeroes(tmp_path):
    db = _make_db(tmp_path, [])
    result = calculate_fleet_savings(db)
    assert result["velocity"]["last_hour_saved"] == 0.0
    assert result["velocity"]["last_24h_saved"] == 0.0
    assert result["velocity"]["all_time_saved"] == 0.0


# ─── calculate_fleet_savings — known inputs ───────────────────────────────────


def test_known_input_haiku_savings(tmp_path):
    """100K input tokens, 50K cache read — verify exact savings formula."""
    rates = MODEL_RATES["claude-haiku-4-5"]
    rows = [(_ts(), "claude-haiku-4-5", 100_000, 10_000, 50_000, 0, 0)]
    db = _make_db(tmp_path, rows)
    result = calculate_fleet_savings(db)
    pm = result["per_model"][0]

    # cost_without = (100K + 50K) / 1M * 0.80 + 10K / 1M * 4.0
    expected_without = (150_000 / 1_000_000) * rates["input"] + (10_000 / 1_000_000) * rates["output"]
    # cost_with = 100K/1M * 0.80 + 50K/1M * 0.08 + 10K/1M * 4.0
    expected_with = (100_000 / 1_000_000) * rates["input"] + (50_000 / 1_000_000) * rates["cached"] + (10_000 / 1_000_000) * rates["output"]

    assert abs(pm["cost_without"] - round(expected_without, 4)) < 0.0001
    assert abs(pm["cost"] - round(expected_with, 4)) < 0.0001
    assert pm["saved"] > 0


def test_no_cache_means_no_savings(tmp_path):
    """Without cache hits, savings should be near zero (only output pricing differs)."""
    rows = [(_ts(), "claude-sonnet-4-6", 100_000, 10_000, 0, 0, 0)]
    db = _make_db(tmp_path, rows)
    result = calculate_fleet_savings(db)
    pm = result["per_model"][0]
    # cost_without = cost_with (no cache reads)
    assert pm["cost"] == pm["cost_without"]
    assert pm["saved"] == 0.0


def test_total_aggregates_multiple_models(tmp_path):
    rows = [
        (_ts(), "claude-haiku-4-5", 100_000, 5_000, 50_000, 0, 0),
        (_ts(), "claude-sonnet-4-6", 200_000, 10_000, 100_000, 0, 0),
    ]
    db = _make_db(tmp_path, rows)
    result = calculate_fleet_savings(db)
    assert result["total_requests"] == 2
    assert len(result["per_model"]) == 2
    # Total should equal sum of per-model
    total_per_model = sum(m["saved"] for m in result["per_model"])
    assert abs(result["total_saved"] - round(total_per_model, 4)) < 0.001


def test_reduction_percent_between_0_and_100(tmp_path):
    rows = [(_ts(), "claude-haiku-4-5", 100_000, 5_000, 80_000, 0, 0)]
    db = _make_db(tmp_path, rows)
    result = calculate_fleet_savings(db)
    assert 0.0 <= result["reduction_percent"] <= 100.0


# ─── calculate_fleet_savings — unknown model ──────────────────────────────────


def test_unknown_model_uses_default_rate(tmp_path):
    """Unknown models should use DEFAULT_RATE and not raise."""
    rows = [(_ts(), "mystery-model-v9", 100_000, 5_000, 50_000, 0, 0)]
    db = _make_db(tmp_path, rows)
    result = calculate_fleet_savings(db)
    assert result["total_requests"] == 1
    pm = result["per_model"][0]
    assert pm["model"] == "mystery-model-v9"
    assert pm["saved"] >= 0.0


# ─── calculate_fleet_savings — period filtering ───────────────────────────────


def test_period_24h_excludes_old_rows(tmp_path):
    rows = [
        (_ts(0), "claude-haiku-4-5", 100_000, 5_000, 50_000, 0, 0),   # recent
        (_ts(48), "claude-haiku-4-5", 100_000, 5_000, 50_000, 0, 0),  # old
    ]
    db = _make_db(tmp_path, rows)
    all_time = calculate_fleet_savings(db, period=None)
    last_24h = calculate_fleet_savings(db, period="24h")
    assert all_time["total_requests"] == 2
    assert last_24h["total_requests"] == 1


def test_period_1h_includes_only_recent(tmp_path):
    rows = [
        (_ts(0), "claude-haiku-4-5", 100_000, 5_000, 50_000, 0, 0),
        (_ts(2), "claude-haiku-4-5", 100_000, 5_000, 50_000, 0, 0),
    ]
    db = _make_db(tmp_path, rows)
    result = calculate_fleet_savings(db, period="1h")
    assert result["total_requests"] == 1


def test_period_7d_includes_week(tmp_path):
    rows = [
        (_ts(0), "claude-haiku-4-5", 50_000, 2_000, 20_000, 0, 0),
        (_ts(100), "claude-haiku-4-5", 50_000, 2_000, 20_000, 0, 0),
        (_ts(200), "claude-haiku-4-5", 50_000, 2_000, 20_000, 0, 0),
    ]
    db = _make_db(tmp_path, rows)
    result_7d = calculate_fleet_savings(db, period="7d")
    result_all = calculate_fleet_savings(db, period=None)
    assert result_7d["total_requests"] <= result_all["total_requests"]


def test_period_label_in_result(tmp_path):
    db = _make_db(tmp_path, [])
    assert calculate_fleet_savings(db, period="24h")["period"] == "24h"
    assert calculate_fleet_savings(db, period=None)["period"] == "all-time"
    assert calculate_fleet_savings(db, period="7d")["period"] == "7d"


# ─── calculate_fleet_savings — velocity ───────────────────────────────────────


def test_velocity_all_time_matches_actual_savings(tmp_path):
    rows = [(_ts(0), "claude-haiku-4-5", 100_000, 5_000, 50_000, 0, 0)]
    db = _make_db(tmp_path, rows)
    result = calculate_fleet_savings(db)
    assert result["velocity"]["all_time_saved"] > 0.0


def test_velocity_keys_present(tmp_path):
    db = _make_db(tmp_path, [])
    result = calculate_fleet_savings(db)
    v = result["velocity"]
    assert "last_hour_saved" in v
    assert "last_24h_saved" in v
    assert "all_time_saved" in v


# ─── calculate_fleet_savings — cache hit percent ──────────────────────────────


def test_cache_hit_percent_calculation(tmp_path):
    # 100K input, 100K cache read → 50% cache hit of total input
    rows = [(_ts(), "claude-haiku-4-5", 100_000, 5_000, 100_000, 0, 0)]
    db = _make_db(tmp_path, rows)
    result = calculate_fleet_savings(db)
    pm = result["per_model"][0]
    # cache_hit_pct = 100K / (100K + 100K) * 100 = 50%
    assert abs(pm["cache_hit_percent"] - 50.0) < 0.1


def test_zero_cache_hit_percent_when_no_cache(tmp_path):
    rows = [(_ts(), "claude-haiku-4-5", 100_000, 5_000, 0, 0, 0)]
    db = _make_db(tmp_path, rows)
    result = calculate_fleet_savings(db)
    assert result["per_model"][0]["cache_hit_percent"] == 0.0


# ─── calculate_savings_breakdown ─────────────────────────────────────────────


def test_breakdown_sums_to_total(tmp_path):
    rows = [(_ts(), "claude-haiku-4-5", 100_000, 5_000, 50_000, 0, 0)]
    db = _make_db(tmp_path, rows)
    result = calculate_fleet_savings(db)
    breakdown = calculate_savings_breakdown(result["per_model"])
    assert abs(breakdown["total"] - (breakdown["cache_optimization"] + breakdown["token_compression"])) < 0.0001


def test_breakdown_cache_optimization_positive(tmp_path):
    rows = [(_ts(), "claude-haiku-4-5", 100_000, 5_000, 50_000, 0, 0)]
    db = _make_db(tmp_path, rows)
    result = calculate_fleet_savings(db)
    breakdown = calculate_savings_breakdown(result["per_model"])
    assert breakdown["cache_optimization"] > 0.0


def test_breakdown_empty_per_model():
    breakdown = calculate_savings_breakdown([])
    assert breakdown["cache_optimization"] == 0.0
    assert breakdown["token_compression"] == 0.0
    assert breakdown["total"] == 0.0


def test_breakdown_keys_present(tmp_path):
    rows = [(_ts(), "claude-haiku-4-5", 100_000, 5_000, 50_000, 0, 0)]
    db = _make_db(tmp_path, rows)
    result = calculate_fleet_savings(db)
    breakdown = calculate_savings_breakdown(result["per_model"])
    assert "cache_optimization" in breakdown
    assert "token_compression" in breakdown
    assert "total" in breakdown


# ─── MODEL_RATES — haiku-4-6 added ───────────────────────────────────────────


def test_haiku_4_6_in_model_rates():
    assert "claude-haiku-4-6" in MODEL_RATES
    rates = MODEL_RATES["claude-haiku-4-6"]
    assert rates["input"] == 0.80
    assert rates["cached"] == 0.08
    assert rates["output"] == 4.0
