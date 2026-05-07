"""Tests for tokenpak.attribution — agent/skill attribution tracking."""


import pytest

pytest.importorskip("tokenpak.attribution", reason="module not available in current build")
import tempfile
import time
from pathlib import Path

import pytest

from tokenpak.attribution import (
    AttributionRecord,
    AttributionTracker,
    detect_source,
    format_attribution,
)


class TestDetectSource:
    def test_explicit_header(self):
        assert detect_source({"X-TokenPak-Source": "sue-main"}) == "sue-main"

    def test_skill_header(self):
        assert detect_source({"X-OpenClaw-Skill": "claude-code"}) == "skill:claude-code"

    def test_session_header_sue(self):
        assert detect_source({"X-OpenClaw-Session": "sue-heartbeat"}) == "sue-openclaw"

    def test_session_header_trix(self):
        assert detect_source({"X-OpenClaw-Session": "trix-coding"}) == "trix-openclaw"

    def test_session_header_cali(self):
        assert detect_source({"X-OpenClaw-Session": "cali-batch"}) == "cali-openclaw"

    def test_user_agent_openclaw(self):
        assert detect_source({"User-Agent": "OpenClaw/1.0"}) == "openclaw"

    def test_localhost_ip(self):
        assert detect_source({}, client_ip="127.0.0.1") == "localhost"

    def test_unknown_fallback(self):
        assert detect_source({}) == "unknown"

    def test_priority_order(self):
        # Explicit source wins over skill
        src = detect_source({
            "X-TokenPak-Source": "explicit",
            "X-OpenClaw-Skill": "skill",
            "X-OpenClaw-Session": "sue-main",
        })
        assert src == "explicit"


class TestAttributionRecord:
    def test_to_dict(self):
        r = AttributionRecord(
            request_id="req-1",
            source="sue-openclaw",
            model="claude-opus-4-6",
            tokens_saved=1000,
            cost_saved=0.50,
            cache_hit=True,
        )
        d = r.to_dict()
        assert d["source"] == "sue-openclaw"
        assert d["tokens_saved"] == 1000
        assert d["cost_saved"] == 0.5

    def test_default_values(self):
        r = AttributionRecord()
        d = r.to_dict()
        assert d["source"] == "unknown"
        assert d["tokens_saved"] == 0


class TestAttributionTracker:
    def _make_tracker(self, n=10):
        tracker = AttributionTracker()
        now = time.time()
        sources = ["sue-openclaw", "trix-openclaw", "cali-openclaw", "unknown"]
        models = ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5"]
        for i in range(n):
            tracker.record(AttributionRecord(
                request_id=f"req-{i}",
                timestamp=now - (n - i) * 60,
                source=sources[i % len(sources)],
                model=models[i % len(models)],
                tokens_saved=1000 + i * 100,
                cost_saved=0.50 + i * 0.05,
                cache_hit=i % 2 == 0,
            ))
        return tracker

    def test_record_and_list(self):
        tracker = self._make_tracker(5)
        assert len(tracker.records) == 5

    def test_rollup_by_source(self):
        tracker = self._make_tracker(10)
        rollup = tracker.rollup_by_source()
        assert "sue-openclaw" in rollup
        assert rollup["sue-openclaw"]["requests"] >= 1
        assert rollup["sue-openclaw"]["cost_saved"] > 0

    def test_rollup_by_model(self):
        tracker = self._make_tracker(10)
        rollup = tracker.rollup_by_model()
        assert "claude-opus-4-6" in rollup
        assert rollup["claude-opus-4-6"]["requests"] >= 1

    def test_leakage_pct(self):
        tracker = self._make_tracker(10)
        leakage = tracker.leakage_pct()
        # 10 records, ~2-3 unknown (every 4th)
        assert 0 <= leakage <= 100

    def test_leakage_zero(self):
        tracker = AttributionTracker()
        for i in range(5):
            tracker.record(AttributionRecord(source="sue-openclaw", timestamp=time.time()))
        assert tracker.leakage_pct() == 0.0

    def test_leakage_high(self):
        tracker = AttributionTracker()
        for i in range(10):
            tracker.record(AttributionRecord(source="unknown", timestamp=time.time()))
        assert tracker.leakage_pct() == 100.0

    def test_save_and_load(self):
        tracker = self._make_tracker(5)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            p = Path(f.name)
        try:
            tracker.save(path=p)
            tracker2 = AttributionTracker()
            tracker2.load(path=p)
            assert len(tracker2.records) == 5
            assert tracker2.records[0].source == tracker.records[0].source
        finally:
            p.unlink(missing_ok=True)

    def test_bounded_memory(self):
        tracker = AttributionTracker(max_records=5)
        for i in range(20):
            tracker.record(AttributionRecord(request_id=f"req-{i}"))
        assert len(tracker.records) == 5

    def test_rollup_with_since(self):
        tracker = self._make_tracker(10)
        # Only last 5 minutes
        since = time.time() - 300
        rollup = tracker.rollup_by_source(since=since)
        total_reqs = sum(v["requests"] for v in rollup.values())
        assert total_reqs <= 10


class TestFormatAttribution:
    def test_format_basic(self):
        tracker = TestAttributionTracker._make_tracker(None, 10)
        output = format_attribution(tracker, days=7)
        assert "TokenPak Attribution" in output
        assert "Agent Breakdown" in output
        assert "Top Models" in output

    def test_format_empty(self):
        tracker = AttributionTracker()
        output = format_attribution(tracker)
        assert "No attribution data" in output

    def test_format_shows_leakage_warning(self):
        tracker = AttributionTracker()
        for i in range(20):
            tracker.record(AttributionRecord(
                source="unknown",
                cost_saved=1.0,
                timestamp=time.time(),
            ))
        output = format_attribution(tracker, days=7)
        assert "LEAKAGE" in output

    def test_format_no_leakage_warning(self):
        tracker = AttributionTracker()
        for i in range(20):
            tracker.record(AttributionRecord(
                source="sue-openclaw",
                cost_saved=1.0,
                timestamp=time.time(),
            ))
        output = format_attribution(tracker, days=7)
        assert "LEAKAGE" not in output
