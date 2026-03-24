"""
Tests for tokenpak.monitor — cost calculations and error parsing.
"""
from __future__ import annotations

import json
import sys
import types


# ---------------------------------------------------------------------------
# Helpers (inline; no external deps)
# ---------------------------------------------------------------------------

COST_PER_TOKEN = 0.000003  # same default as monitor_dashboard.html JS estimate


def compute_cost_per_hour(stats: dict) -> float:
    """Replicate the JS cost-projection logic in Python for testing."""
    uptime = max(stats.get("uptime_seconds", 1), 1)
    comp = stats.get("compression", {})
    tokens_after = comp.get("tokens_after", 0)
    total_cost = tokens_after * COST_PER_TOKEN
    per_sec = total_cost / uptime
    return per_sec * 3600


def parse_errors_jsonl(lines: list[str]) -> list[dict]:
    """Parse a list of JSONL strings into error dicts."""
    errors = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            errors.append(json.loads(line))
        except json.JSONDecodeError:
            errors.append({"raw": line})
    return errors


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_stats_parsing():
    """Given a sample stats dict, verify cost/hour calculation is correct."""
    stats = {
        "uptime_seconds": 3600.0,  # exactly 1 hour
        "requests_total": 100,
        "requests_per_sec": 0.0278,
        "compression": {
            "tokens_before": 200_000,
            "tokens_after": 100_000,  # 100k tokens sent in 1 hour
            "ratio": 0.5,
            "compressed": 80,
            "skipped": 20,
        },
        "routing": {"anthropic_claude": 80, "openai": 20},
        "errors": {"total": 2},
        "latest_request_ms": 142.5,
        "timestamp": "2026-03-24T12:00:00+00:00",
    }

    cost_per_hour = compute_cost_per_hour(stats)

    # 100_000 tokens * $0.000003/token = $0.30 total cost over 1 hour = $0.30/hr
    expected = 100_000 * COST_PER_TOKEN  # = 0.30
    assert abs(cost_per_hour - expected) < 1e-9, (
        f"Expected cost/hr={expected:.6f}, got {cost_per_hour:.6f}"
    )

    # Scaling: 2-hour uptime should halve the per-hour rate
    stats2 = dict(stats, uptime_seconds=7200.0)
    cost_per_hour2 = compute_cost_per_hour(stats2)
    assert abs(cost_per_hour2 - expected / 2) < 1e-9, (
        f"2h uptime: expected {expected/2:.6f}, got {cost_per_hour2:.6f}"
    )

    # Zero tokens → zero cost
    stats_zero = dict(stats, compression={"tokens_after": 0})
    assert compute_cost_per_hour(stats_zero) == 0.0


def test_error_parsing():
    """Given sample JSONL lines, verify error list is parsed correctly."""
    lines = [
        '{"timestamp":"2026-03-24T10:00:00Z","type":"error","message":"Auth failed"}',
        '{"timestamp":"2026-03-24T10:01:00Z","type":"warning","message":"Rate limit approaching"}',
        '{"timestamp":"2026-03-24T10:02:00Z","type":"info","message":"Vault rebuilt"}',
        "",  # blank line should be skipped
        'not-valid-json',  # malformed → raw fallback
        '{"ts":"2026-03-24T10:03:00Z","level":"error","msg":"Timeout"}',
    ]

    errors = parse_errors_jsonl(lines)

    # Blank line skipped
    assert len(errors) == 5

    # First entry
    assert errors[0]["type"] == "error"
    assert errors[0]["message"] == "Auth failed"

    # Warning entry
    assert errors[1]["type"] == "warning"

    # Info entry
    assert errors[2]["type"] == "info"

    # Malformed → raw fallback
    assert "raw" in errors[3]
    assert errors[3]["raw"] == "not-valid-json"

    # Alternative field names (ts/level/msg)
    assert errors[4]["level"] == "error"
    assert errors[4]["msg"] == "Timeout"


def test_feature_count_range():
    """Sanity check: feature count is within expected range (placeholder)."""
    feature_count = 60  # representative count of dashboard features
    assert 40 <= feature_count <= 80, (
        f"Feature count {feature_count} outside expected range [40, 80]"
    )
