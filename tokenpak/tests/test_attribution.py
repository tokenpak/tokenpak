"""
Unit tests for tokenpak/attribution.py.

Covers:
- AttributionRecord.to_dict()
- detect_source() — all 6 priority branches
- AttributionTracker.record(), .records, .rollup_by_source(), .rollup_by_model()
- AttributionTracker.leakage_pct()
- AttributionTracker.save() / .load()
- format_attribution()
"""

import json
import tempfile
import time
import unittest
from pathlib import Path

from tokenpak.attribution import (
    AGENT_EMOJI,
    AttributionRecord,
    AttributionTracker,
    detect_source,
    format_attribution,
)


class TestAttributionRecord(unittest.TestCase):

    def test_to_dict_contains_all_fields(self):
        rec = AttributionRecord(
            request_id="req-1",
            timestamp=1000.0,
            source="trix-openclaw",
            model="claude-sonnet-4-6",
            tokens_saved=500,
            cost_saved=0.0123456,
            cache_hit=True,
            compression_pct=12.345,
        )
        d = rec.to_dict()
        for key in ("request_id", "timestamp", "source", "model",
                    "tokens_saved", "cost_saved", "cache_hit", "compression_pct"):
            self.assertIn(key, d)

    def test_to_dict_rounds_cost_saved(self):
        rec = AttributionRecord(cost_saved=0.0123456789)
        d = rec.to_dict()
        self.assertEqual(d["cost_saved"], round(0.0123456789, 6))

    def test_to_dict_rounds_compression_pct(self):
        rec = AttributionRecord(compression_pct=12.3456789)
        d = rec.to_dict()
        self.assertEqual(d["compression_pct"], round(12.3456789, 2))

    def test_default_values(self):
        rec = AttributionRecord()
        self.assertEqual(rec.source, "unknown")
        self.assertEqual(rec.tokens_saved, 0)
        self.assertFalse(rec.cache_hit)


class TestDetectSource(unittest.TestCase):

    def test_explicit_tokenpak_source_header(self):
        src = detect_source(headers={"X-TokenPak-Source": "my-app"})
        self.assertEqual(src, "my-app")

    def test_skill_header(self):
        src = detect_source(headers={"X-OpenClaw-Skill": "weather"})
        self.assertEqual(src, "skill:weather")

    def test_session_header_with_agent_name_trix(self):
        src = detect_source(headers={"X-OpenClaw-Session": "trix-heartbeat"})
        self.assertEqual(src, "trix-openclaw")

    def test_session_header_with_agent_name_sue(self):
        src = detect_source(headers={"X-OpenClaw-Session": "sue-main"})
        self.assertEqual(src, "sue-openclaw")

    def test_session_header_no_agent_name(self):
        src = detect_source(headers={"X-OpenClaw-Session": "unknown-session-123"})
        self.assertTrue(src.startswith("session:"))

    def test_user_agent_openclaw(self):
        src = detect_source(user_agent="OpenClaw/1.0")
        self.assertEqual(src, "openclaw")

    def test_user_agent_codex(self):
        src = detect_source(user_agent="codex-agent/2.0")
        self.assertEqual(src, "coding-agent")

    def test_localhost_ip(self):
        src = detect_source(client_ip="127.0.0.1")
        self.assertEqual(src, "localhost")

    def test_unknown_fallback(self):
        src = detect_source()
        self.assertEqual(src, "unknown")

    def test_explicit_source_takes_priority_over_skill(self):
        src = detect_source(headers={
            "X-TokenPak-Source": "explicit",
            "X-OpenClaw-Skill": "weather",
        })
        self.assertEqual(src, "explicit")

    def test_none_headers_safe(self):
        src = detect_source(headers=None)
        self.assertEqual(src, "unknown")


class TestAttributionTracker(unittest.TestCase):

    def _make_rec(self, source="trix-openclaw", model="claude-sonnet-4-6",
                  tokens_saved=100, cost_saved=0.01, cache_hit=False, ts=None):
        return AttributionRecord(
            request_id="req",
            timestamp=ts or time.time(),
            source=source,
            model=model,
            tokens_saved=tokens_saved,
            cost_saved=cost_saved,
            cache_hit=cache_hit,
        )

    def test_record_adds_to_records(self):
        tracker = AttributionTracker()
        tracker.record(self._make_rec())
        self.assertEqual(len(tracker.records), 1)

    def test_record_autotimestamps(self):
        tracker = AttributionTracker()
        rec = AttributionRecord(timestamp=0.0)
        before = time.time()
        tracker.record(rec)
        self.assertGreaterEqual(rec.timestamp, before)

    def test_max_records_enforced(self):
        tracker = AttributionTracker(max_records=3)
        for i in range(5):
            tracker.record(self._make_rec())
        self.assertEqual(len(tracker.records), 3)

    def test_rollup_by_source_basic(self):
        tracker = AttributionTracker()
        tracker.record(self._make_rec(source="sue-openclaw", tokens_saved=200, cost_saved=0.05, cache_hit=True))
        tracker.record(self._make_rec(source="sue-openclaw", tokens_saved=100, cost_saved=0.03))
        rollup = tracker.rollup_by_source()
        self.assertIn("sue-openclaw", rollup)
        entry = rollup["sue-openclaw"]
        self.assertEqual(entry["requests"], 2)
        self.assertEqual(entry["tokens_saved"], 300)
        self.assertAlmostEqual(entry["cost_saved"], 0.08, places=4)
        self.assertEqual(entry["cache_hit_rate"], 0.5)

    def test_rollup_by_source_since_filter(self):
        tracker = AttributionTracker()
        old_ts = time.time() - 3600
        tracker.record(self._make_rec(source="old-agent", ts=old_ts))
        tracker.record(self._make_rec(source="new-agent"))
        rollup = tracker.rollup_by_source(since=time.time() - 60)
        self.assertIn("new-agent", rollup)
        self.assertNotIn("old-agent", rollup)

    def test_rollup_by_model(self):
        tracker = AttributionTracker()
        tracker.record(self._make_rec(model="claude-haiku-4-5", cost_saved=0.001))
        tracker.record(self._make_rec(model="claude-sonnet-4-6", cost_saved=0.05))
        rollup = tracker.rollup_by_model()
        self.assertIn("claude-sonnet-4-6", rollup)
        self.assertIn("claude-haiku-4-5", rollup)
        # Sorted by cost_saved descending — sonnet first
        keys = list(rollup.keys())
        self.assertEqual(keys[0], "claude-sonnet-4-6")

    def test_leakage_pct_all_unknown(self):
        tracker = AttributionTracker()
        for _ in range(4):
            tracker.record(self._make_rec(source="unknown"))
        self.assertEqual(tracker.leakage_pct(), 100.0)

    def test_leakage_pct_no_unknown(self):
        tracker = AttributionTracker()
        tracker.record(self._make_rec(source="trix-openclaw"))
        self.assertEqual(tracker.leakage_pct(), 0.0)

    def test_leakage_pct_empty(self):
        tracker = AttributionTracker()
        self.assertEqual(tracker.leakage_pct(), 0.0)

    def test_save_and_load_roundtrip(self):
        tracker = AttributionTracker()
        tracker.record(self._make_rec(source="trix-openclaw", tokens_saved=999, cost_saved=0.123))
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            p = Path(f.name)
        tracker.save(path=p)

        tracker2 = AttributionTracker()
        tracker2.load(path=p)
        self.assertEqual(len(tracker2.records), 1)
        loaded = tracker2.records[0]
        self.assertEqual(loaded.source, "trix-openclaw")
        self.assertEqual(loaded.tokens_saved, 999)
        p.unlink()

    def test_load_missing_file_is_safe(self):
        tracker = AttributionTracker()
        tracker.load(path=Path("/nonexistent/path/attribution.json"))
        self.assertEqual(len(tracker.records), 0)

    def test_load_corrupt_json_is_safe(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            f.write("not-valid-json{{{")
            p = Path(f.name)
        tracker = AttributionTracker()
        tracker.load(path=p)  # should not raise
        self.assertEqual(len(tracker.records), 0)
        p.unlink()


class TestFormatAttribution(unittest.TestCase):

    def test_empty_tracker_returns_no_data_message(self):
        tracker = AttributionTracker()
        result = format_attribution(tracker)
        self.assertIn("No attribution data", result)

    def test_format_contains_agent_source(self):
        tracker = AttributionTracker()
        rec = AttributionRecord(
            request_id="r1", timestamp=time.time(),
            source="trix-openclaw", model="claude-sonnet-4-6",
            tokens_saved=500, cost_saved=0.05, cache_hit=True,
        )
        tracker.record(rec)
        result = format_attribution(tracker, days=1)
        self.assertIn("trix-openclaw", result)

    def test_format_contains_model(self):
        tracker = AttributionTracker()
        rec = AttributionRecord(
            request_id="r1", timestamp=time.time(),
            source="sue-openclaw", model="claude-opus-4-6",
            tokens_saved=1000, cost_saved=0.5,
        )
        tracker.record(rec)
        result = format_attribution(tracker, days=1)
        self.assertIn("claude-opus-4-6", result)

    def test_leakage_warning_shown_above_5pct(self):
        tracker = AttributionTracker()
        for _ in range(10):
            rec = AttributionRecord(
                timestamp=time.time(),
                source="unknown", model="claude-sonnet-4-6",
                tokens_saved=10, cost_saved=0.001,
            )
            tracker.record(rec)
        result = format_attribution(tracker, days=1)
        self.assertIn("LEAKAGE", result)


if __name__ == "__main__":
    unittest.main()
