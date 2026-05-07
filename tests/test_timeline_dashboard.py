"""Tests for tokenpak.timeline — savings timeline and trend analysis."""


import pytest

pytest.importorskip("tokenpak.timeline", reason="module not available in current build")
import json
import tempfile
from pathlib import Path

import pytest

from tokenpak.timeline import (
    compute_trends,
    detect_anomalies,
    format_timeline,
    get_timeline,
    load_history,
    render_chart,
    save_snapshot,
)


def _make_entries(n=7, base_saved=50.0):
    """Generate mock daily entries."""
    entries = []
    for i in range(n):
        entries.append({
            "date": f"2026-03-{11 - i:02d}",
            "requests": 200 + i * 10,
            "saved_usd": base_saved + (i % 3 - 1) * 10,
            "cache_hit_pct": 90 + (i % 5),
            "compression_pct": 5.0 + (i % 3),
        })
    return entries


class TestLoadSave:
    def test_save_and_load(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            p = Path(f.name)
        try:
            save_snapshot({"date": "2026-03-11", "saved_usd": 50.0}, path=p)
            save_snapshot({"date": "2026-03-10", "saved_usd": 40.0}, path=p)
            entries = load_history(path=p)
            assert len(entries) == 2
            assert entries[0]["date"] == "2026-03-11"
        finally:
            p.unlink(missing_ok=True)

    def test_load_missing_file(self):
        entries = load_history(path=Path("/tmp/nonexistent_history.jsonl"))
        assert entries == []


class TestGetTimeline:
    def test_get_timeline_7_days(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            p = Path(f.name)
        try:
            for e in _make_entries(10):
                save_snapshot(e, path=p)
            result = get_timeline(days=7, path=p)
            assert len(result) == 7
            # Should be sorted newest first
            assert result[0]["date"] >= result[-1]["date"]
        finally:
            p.unlink(missing_ok=True)

    def test_get_timeline_30_days(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            p = Path(f.name)
        try:
            for e in _make_entries(10):
                save_snapshot(e, path=p)
            result = get_timeline(days=30, path=p)
            assert len(result) == 10  # only 10 entries exist
        finally:
            p.unlink(missing_ok=True)


class TestComputeTrends:
    def test_trends_have_arrows(self):
        entries = _make_entries(7)
        result = compute_trends(entries)
        assert all("trend" in e for e in result)
        # Last entry should be "—"
        assert result[-1]["trend"] == "—"

    def test_trends_direction(self):
        entries = [
            {"date": "2026-03-11", "saved_usd": 100},
            {"date": "2026-03-10", "saved_usd": 50},
        ]
        result = compute_trends(entries)
        assert "↗" in result[0]["trend"]  # 100 > 50, +100%

    def test_trends_decline(self):
        entries = [
            {"date": "2026-03-11", "saved_usd": 30},
            {"date": "2026-03-10", "saved_usd": 60},
        ]
        result = compute_trends(entries)
        assert "↘" in result[0]["trend"]  # 30 < 60, -50%

    def test_trends_steady(self):
        entries = [
            {"date": "2026-03-11", "saved_usd": 50},
            {"date": "2026-03-10", "saved_usd": 50},
        ]
        result = compute_trends(entries)
        assert result[0]["trend"] == "—"


class TestAnomalyDetection:
    def test_no_anomalies_normal_data(self):
        entries = [{"saved_usd": 50}, {"saved_usd": 48}, {"saved_usd": 52}]
        anomalies = detect_anomalies(entries)
        assert len(anomalies) == 0

    def test_detect_anomaly(self):
        entries = [
            {"date": "2026-03-11", "saved_usd": 50, "cache_hit_pct": 95},
            {"date": "2026-03-10", "saved_usd": 48, "cache_hit_pct": 94},
            {"date": "2026-03-09", "saved_usd": 52, "cache_hit_pct": 96},
            {"date": "2026-03-08", "saved_usd": 5, "cache_hit_pct": 30},  # anomaly!
            {"date": "2026-03-07", "saved_usd": 49, "cache_hit_pct": 93},
        ]
        anomalies = detect_anomalies(entries, threshold=1.5)
        assert len(anomalies) >= 1
        assert anomalies[0]["date"] == "2026-03-08"

    def test_too_few_entries(self):
        entries = [{"saved_usd": 50}, {"saved_usd": 48}]
        anomalies = detect_anomalies(entries)
        assert anomalies == []


class TestRenderChart:
    def test_chart_renders(self):
        entries = _make_entries(7)
        chart = render_chart(entries)
        assert "Trend:" in chart
        assert "$" in chart

    def test_chart_empty(self):
        chart = render_chart([])
        assert "No data" in chart

    def test_chart_single_entry(self):
        chart = render_chart([{"saved_usd": 50}])
        assert "Trend:" in chart


class TestFormatTimeline:
    def test_format_7_days(self):
        entries = _make_entries(7)
        output = format_timeline(entries)
        assert "TokenPak Savings Timeline" in output
        assert "Last 7 Days" in output
        assert "Mar" in output
        assert "Average" in output

    def test_format_with_chart(self):
        entries = _make_entries(7)
        output = format_timeline(entries, show_chart=True)
        assert "Trend:" in output

    def test_format_empty(self):
        output = format_timeline([])
        assert "No history" in output

    def test_format_shows_best_worst(self):
        entries = _make_entries(7)
        output = format_timeline(entries)
        assert "Best day" in output
        assert "Worst day" in output

    def test_format_shows_anomaly(self):
        # Need tight cluster + extreme outlier for 2σ detection
        entries = [
            {"date": "2026-03-11", "saved_usd": 50, "cache_hit_pct": 95, "requests": 200, "compression_pct": 5},
            {"date": "2026-03-10", "saved_usd": 50, "cache_hit_pct": 94, "requests": 190, "compression_pct": 4.5},
            {"date": "2026-03-09", "saved_usd": 50, "cache_hit_pct": 96, "requests": 210, "compression_pct": 5.5},
            {"date": "2026-03-08", "saved_usd": 50, "cache_hit_pct": 95, "requests": 200, "compression_pct": 5},
            {"date": "2026-03-07", "saved_usd": 50, "cache_hit_pct": 95, "requests": 200, "compression_pct": 5},
            {"date": "2026-03-06", "saved_usd": 1, "cache_hit_pct": 10, "requests": 50, "compression_pct": 1},
            {"date": "2026-03-05", "saved_usd": 50, "cache_hit_pct": 95, "requests": 200, "compression_pct": 5},
        ]
        output = format_timeline(entries)
        assert "Anomaly" in output or "anomaly" in output.lower()


class TestJsonOutput:
    def test_json_valid(self):
        entries = _make_entries(7)
        json_str = json.dumps(entries)
        parsed = json.loads(json_str)
        assert len(parsed) == 7
        assert "date" in parsed[0]
        assert "saved_usd" in parsed[0]
