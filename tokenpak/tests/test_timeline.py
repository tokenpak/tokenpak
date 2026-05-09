"""test_timeline.py — Unit tests for timeline.py (savings history + trends)."""

import json

from tokenpak import timeline


class TestLoadHistory:
    """Tests for load_history()."""

    def test_load_empty_history_nonexistent_file(self, tmp_path):
        """Empty list returned when history file does not exist."""
        history_path = tmp_path / "nonexistent.jsonl"
        result = timeline.load_history(history_path)
        assert result == []

    def test_load_single_entry(self, tmp_path):
        """Single valid JSON entry is loaded."""
        history_path = tmp_path / "history.jsonl"
        entry = {"date": "2026-03-27", "saved_usd": 10.5, "requests": 100}
        history_path.write_text(json.dumps(entry) + "\n")

        result = timeline.load_history(history_path)
        assert len(result) == 1
        assert result[0] == entry

    def test_load_multiple_entries(self, tmp_path):
        """Multiple entries in JSONL format are all loaded."""
        history_path = tmp_path / "history.jsonl"
        entries = [
            {"date": "2026-03-25", "saved_usd": 5.0, "requests": 50},
            {"date": "2026-03-26", "saved_usd": 8.0, "requests": 80},
            {"date": "2026-03-27", "saved_usd": 10.5, "requests": 100},
        ]
        history_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        result = timeline.load_history(history_path)
        assert len(result) == 3
        assert result == entries

    def test_load_skip_malformed_json(self, tmp_path):
        """Malformed JSON lines are skipped gracefully."""
        history_path = tmp_path / "history.jsonl"
        lines = [
            json.dumps({"date": "2026-03-25", "saved_usd": 5.0}),
            "invalid json line {{{",
            json.dumps({"date": "2026-03-27", "saved_usd": 10.5}),
        ]
        history_path.write_text("\n".join(lines) + "\n")

        result = timeline.load_history(history_path)
        assert len(result) == 2
        assert result[0]["date"] == "2026-03-25"
        assert result[1]["date"] == "2026-03-27"

    def test_load_empty_file(self, tmp_path):
        """Empty file returns empty list."""
        history_path = tmp_path / "history.jsonl"
        history_path.write_text("")

        result = timeline.load_history(history_path)
        assert result == []


class TestSaveSnapshot:
    """Tests for save_snapshot()."""

    def test_save_creates_file(self, tmp_path):
        """save_snapshot() creates the file if it doesn't exist."""
        history_path = tmp_path / "history.jsonl"
        snapshot = {"date": "2026-03-27", "saved_usd": 10.5}

        timeline.save_snapshot(snapshot, history_path)
        assert history_path.exists()

    def test_save_creates_parent_dirs(self, tmp_path):
        """save_snapshot() creates parent directories if missing."""
        history_path = tmp_path / "subdir" / "nested" / "history.jsonl"
        snapshot = {"date": "2026-03-27", "saved_usd": 10.5}

        timeline.save_snapshot(snapshot, history_path)
        assert history_path.exists()
        assert history_path.parent.exists()

    def test_save_appends_to_existing(self, tmp_path):
        """save_snapshot() appends to existing file, doesn't overwrite."""
        history_path = tmp_path / "history.jsonl"
        snap1 = {"date": "2026-03-26", "saved_usd": 5.0}
        snap2 = {"date": "2026-03-27", "saved_usd": 10.5}

        timeline.save_snapshot(snap1, history_path)
        timeline.save_snapshot(snap2, history_path)

        result = timeline.load_history(history_path)
        assert len(result) == 2
        assert result[0] == snap1
        assert result[1] == snap2

    def test_save_round_trip(self, tmp_path):
        """save_snapshot() + load_history() round-trip preserves data."""
        history_path = tmp_path / "history.jsonl"
        original = {
            "date": "2026-03-27",
            "saved_usd": 12.34,
            "requests": 123,
            "cache_hit_pct": 45.6,
        }

        timeline.save_snapshot(original, history_path)
        loaded = timeline.load_history(history_path)

        assert len(loaded) == 1
        assert loaded[0] == original


class TestGetTimeline:
    """Tests for get_timeline()."""

    def test_get_timeline_empty(self, tmp_path):
        """Empty history returns empty timeline."""
        history_path = tmp_path / "history.jsonl"
        result = timeline.get_timeline(days=7, path=history_path)
        assert result == []

    def test_get_timeline_limited_to_days(self, tmp_path):
        """get_timeline() respects days limit."""
        history_path = tmp_path / "history.jsonl"
        entries = [
            {"date": f"2026-03-{20+i:02d}", "saved_usd": i * 5.0}
            for i in range(10)
        ]
        history_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        result = timeline.get_timeline(days=3, path=history_path)
        assert len(result) == 3

    def test_get_timeline_sorted_newest_first(self, tmp_path):
        """get_timeline() returns entries sorted newest first."""
        history_path = tmp_path / "history.jsonl"
        entries = [
            {"date": "2026-03-25", "saved_usd": 5.0},
            {"date": "2026-03-27", "saved_usd": 10.5},
            {"date": "2026-03-26", "saved_usd": 8.0},
        ]
        history_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        result = timeline.get_timeline(days=10, path=history_path)
        assert len(result) == 3
        assert result[0]["date"] == "2026-03-27"  # Newest
        assert result[1]["date"] == "2026-03-26"
        assert result[2]["date"] == "2026-03-25"  # Oldest


class TestComputeTrends:
    """Tests for compute_trends()."""

    def test_compute_trends_single_entry_no_prev(self):
        """Single entry has no previous to compare, trend is '—'."""
        entries = [{"date": "2026-03-27", "saved_usd": 10.0}]
        result = timeline.compute_trends(entries)
        assert result[0]["trend"] == "—"
        assert result[0]["trend_pct"] == 0

    def test_compute_trends_no_change_within_threshold(self):
        """Savings change within ±5% threshold shows '—'."""
        entries = [
            {"date": "2026-03-27", "saved_usd": 10.0},
            {"date": "2026-03-26", "saved_usd": 10.0},
        ]
        result = timeline.compute_trends(entries)
        # Newest first, so result[0] compares to result[1]
        assert result[0]["trend"] == "—"

    def test_compute_trends_increase_above_threshold(self):
        """Savings increase >5% shows '↗' arrow."""
        entries = [
            {"date": "2026-03-27", "saved_usd": 12.0},
            {"date": "2026-03-26", "saved_usd": 10.0},
        ]
        result = timeline.compute_trends(entries)
        assert "↗" in result[0]["trend"]
        assert "+20" in result[0]["trend"]

    def test_compute_trends_decrease_below_threshold(self):
        """Savings decrease <-5% shows '↘' arrow."""
        entries = [
            {"date": "2026-03-27", "saved_usd": 8.0},
            {"date": "2026-03-26", "saved_usd": 10.0},
        ]
        result = timeline.compute_trends(entries)
        assert "↘" in result[0]["trend"]
        assert "-20" in result[0]["trend"]

    def test_compute_trends_zero_prev_saved(self):
        """When prev saved is 0, trend is '—'."""
        entries = [
            {"date": "2026-03-27", "saved_usd": 10.0},
            {"date": "2026-03-26", "saved_usd": 0},
        ]
        result = timeline.compute_trends(entries)
        assert result[0]["trend"] == "—"


class TestDetectAnomalies:
    """Tests for detect_anomalies()."""

    def test_detect_anomalies_too_few_entries(self):
        """Fewer than 3 entries returns empty anomalies list."""
        entries = [{"date": "2026-03-27", "saved_usd": 10.0}]
        result = timeline.detect_anomalies(entries, threshold=2.0)
        assert result == []

    def test_detect_anomalies_zero_average(self):
        """All zeros (avg 0) returns empty anomalies."""
        entries = [
            {"date": "2026-03-25", "saved_usd": 0},
            {"date": "2026-03-26", "saved_usd": 0},
            {"date": "2026-03-27", "saved_usd": 0},
        ]
        result = timeline.detect_anomalies(entries, threshold=2.0)
        assert result == []

    def test_detect_anomalies_flags_low_outlier(self):
        """Entry well below avg-2σ is flagged as anomaly."""
        entries = [
            {"date": "2026-03-25", "saved_usd": 100.0},
            {"date": "2026-03-26", "saved_usd": 100.0},
            {"date": "2026-03-27", "saved_usd": 0.1},  # Outlier, well below 2σ
            {"date": "2026-03-28", "saved_usd": 100.0},
            {"date": "2026-03-29", "saved_usd": 100.0},
        ]
        result = timeline.detect_anomalies(entries, threshold=2.0)
        assert len(result) > 0
        assert any(a["date"] == "2026-03-27" for a in result)

    def test_detect_anomalies_no_flags_when_normal(self):
        """Consistent data with no outliers returns empty."""
        entries = [
            {"date": "2026-03-25", "saved_usd": 10.0},
            {"date": "2026-03-26", "saved_usd": 11.0},
            {"date": "2026-03-27", "saved_usd": 9.5},
            {"date": "2026-03-28", "saved_usd": 10.5},
        ]
        result = timeline.detect_anomalies(entries, threshold=2.0)
        assert result == []


class TestRenderChart:
    """Tests for render_chart()."""

    def test_render_chart_empty_entries(self):
        """Empty entries returns 'No data' message."""
        result = timeline.render_chart([])
        assert "No data" in result

    def test_render_chart_single_entry(self):
        """Single entry produces valid chart output."""
        entries = [{"date": "2026-03-27", "saved_usd": 10.0}]
        result = timeline.render_chart(entries)
        assert "$" in result
        assert "─" in result

    def test_render_chart_multiple_entries(self):
        """Multiple entries produce sparkline."""
        entries = [
            {"date": "2026-03-25", "saved_usd": 5.0},
            {"date": "2026-03-26", "saved_usd": 15.0},
            {"date": "2026-03-27", "saved_usd": 10.0},
        ]
        result = timeline.render_chart(entries)
        assert "$" in result
        assert "▁" in result or "▂" in result or "█" in result  # Some level

    def test_render_chart_steady_classification(self):
        """Consistent data classified as STEADY."""
        entries = [
            {"date": f"2026-03-{20+i:02d}", "saved_usd": 10.0} for i in range(5)
        ]
        result = timeline.render_chart(entries)
        assert "STEADY" in result or "MODERATE" in result


class TestFormatTimeline:
    """Tests for format_timeline()."""

    def test_format_timeline_empty(self):
        """Empty history returns user-friendly message."""
        result = timeline.format_timeline([])
        assert "No history" in result or "history" in result.lower()

    def test_format_timeline_single_entry(self):
        """Single entry produces formatted report."""
        entries = [
            {
                "date": "2026-03-27",
                "saved_usd": 10.5,
                "requests": 100,
                "cache_hit_pct": 45.0,
                "compression_pct": 12.5,
            }
        ]
        result = timeline.format_timeline(entries)
        assert "2026-03-27" in result or "Mar" in result
        assert "10.5" in result
        assert "Average:" in result

    def test_format_timeline_multiple_entries(self):
        """Multiple entries show averages, best/worst days."""
        entries = [
            {
                "date": "2026-03-25",
                "saved_usd": 5.0,
                "requests": 50,
                "cache_hit_pct": 40.0,
                "compression_pct": 10.0,
            },
            {
                "date": "2026-03-26",
                "saved_usd": 15.0,
                "requests": 150,
                "cache_hit_pct": 50.0,
                "compression_pct": 15.0,
            },
            {
                "date": "2026-03-27",
                "saved_usd": 10.0,
                "requests": 100,
                "cache_hit_pct": 45.0,
                "compression_pct": 12.0,
            },
        ]
        result = timeline.format_timeline(entries)
        assert "Average:" in result
        assert "Best day:" in result
        assert "Worst day:" in result
        assert "15.00" in result  # Best

    def test_format_timeline_with_chart(self):
        """With show_chart=True, includes ASCII chart."""
        entries = [
            {"date": "2026-03-25", "saved_usd": 5.0, "requests": 50},
            {"date": "2026-03-26", "saved_usd": 15.0, "requests": 150},
        ]
        result = timeline.format_timeline(entries, show_chart=True)
        assert "─" in result or "█" in result or "Trend:" in result
