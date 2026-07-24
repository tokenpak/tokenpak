"""Unit tests for calibrator.py (Part C — Compression Calibration)."""

import pytest

pytest.importorskip("tokenpak.calibrator", reason="module not available in current build")
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from tokenpak.calibrator import (
    MAX_EVENTS,
    _downgrade_mode,
    _event_weight,
    compute_retry_rate,
    get_effective_mode,
    load_calibration,
    log_retry,
    log_success,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_path(tmpdir):
    return os.path.join(tmpdir, "calibration.json")


def _ts(days_ago: float = 0) -> str:
    """Return ISO timestamp N days in the past."""
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.isoformat()


def _flood_retries(path, risk_class, mode, n, days_ago=0):
    """Log n retry events for a given risk_class + mode."""
    for _ in range(n):
        log_retry("q", mode, [risk_class], calibration_path=path)


# ---------------------------------------------------------------------------
# _event_weight (decay)
# ---------------------------------------------------------------------------


class TestEventWeight:
    def test_fresh_event_weight_1(self):
        assert _event_weight(0) == pytest.approx(1.0)

    def test_weight_1_within_7d(self):
        assert _event_weight(6.9) == pytest.approx(1.0)

    def test_weight_50pct_at_8d(self):
        assert _event_weight(8) == pytest.approx(0.50)

    def test_weight_25pct_at_15d(self):
        assert _event_weight(15) == pytest.approx(0.25)

    def test_weight_zero_at_31d(self):
        assert _event_weight(31) == pytest.approx(0.0)

    def test_weight_zero_at_30d_plus(self):
        assert _event_weight(30.1) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# log_retry + log_success basic
# ---------------------------------------------------------------------------


class TestLogging:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = make_path(self.tmpdir)

    def test_log_retry_creates_file(self):
        log_retry("q", "aggressive", ["CODE"], calibration_path=self.path)
        assert os.path.exists(self.path)

    def test_log_retry_event_stored(self):
        log_retry("find auth", "aggressive", ["CODE", "NARRATIVE"], calibration_path=self.path)
        data = load_calibration(self.path)
        assert len(data["events"]) == 1
        ev = data["events"][0]
        assert ev["type"] == "retry"
        assert ev["mode"] == "aggressive"
        assert "CODE" in ev["risk_classes"]
        assert "NARRATIVE" in ev["risk_classes"]
        assert ev["query"] == "find auth"

    def test_log_success_event_stored(self):
        log_success("find auth", "hybrid", calibration_path=self.path)
        data = load_calibration(self.path)
        assert len(data["events"]) == 1
        ev = data["events"][0]
        assert ev["type"] == "success"
        assert ev["mode"] == "hybrid"

    def test_risk_classes_normalized_uppercase(self):
        log_retry("q", "aggressive", ["code", "narrative"], calibration_path=self.path)
        data = load_calibration(self.path)
        classes = data["events"][0]["risk_classes"]
        assert "CODE" in classes
        assert "NARRATIVE" in classes

    def test_mode_normalized_lowercase(self):
        log_retry("q", "AGGRESSIVE", ["CODE"], calibration_path=self.path)
        data = load_calibration(self.path)
        assert data["events"][0]["mode"] == "aggressive"

    def test_rolling_window_capped_at_max(self):
        for i in range(MAX_EVENTS + 20):
            log_success(f"q{i}", "aggressive", calibration_path=self.path)
        data = load_calibration(self.path)
        assert len(data["events"]) == MAX_EVENTS

    def test_updated_timestamp_set(self):
        log_success("q", "aggressive", calibration_path=self.path)
        data = load_calibration(self.path)
        assert data["updated"] != ""


# ---------------------------------------------------------------------------
# compute_retry_rate + decay
# ---------------------------------------------------------------------------


class TestComputeRetryRate:
    def test_no_events_returns_zero(self):
        rate = compute_retry_rate("CODE", "aggressive", [])
        assert rate == pytest.approx(0.0)

    def test_all_retries_returns_one(self):
        now = datetime.now(timezone.utc)
        events = [
            {"type": "retry", "mode": "aggressive", "risk_classes": ["CODE"], "timestamp": _ts(0)}
            for _ in range(5)
        ]
        rate = compute_retry_rate("CODE", "aggressive", events, now=now)
        assert rate == pytest.approx(1.0)

    def test_no_retries_returns_zero(self):
        now = datetime.now(timezone.utc)
        events = [
            {"type": "success", "mode": "aggressive", "risk_classes": [], "timestamp": _ts(0)}
            for _ in range(5)
        ]
        rate = compute_retry_rate("CODE", "aggressive", events, now=now)
        assert rate == pytest.approx(0.0)

    def test_mixed_50pct_rate(self):
        now = datetime.now(timezone.utc)
        events = [
            {"type": "retry", "mode": "aggressive", "risk_classes": ["CODE"], "timestamp": _ts(0)}
        ] * 5 + [
            {"type": "success", "mode": "aggressive", "risk_classes": [], "timestamp": _ts(0)}
        ] * 5
        rate = compute_retry_rate("CODE", "aggressive", events, now=now)
        assert rate == pytest.approx(0.5)

    def test_wrong_mode_not_counted(self):
        now = datetime.now(timezone.utc)
        events = [
            {"type": "retry", "mode": "hybrid", "risk_classes": ["CODE"], "timestamp": _ts(0)}
            for _ in range(10)
        ]
        rate = compute_retry_rate("CODE", "aggressive", events, now=now)
        assert rate == pytest.approx(0.0)

    def test_wrong_risk_class_not_counted(self):
        now = datetime.now(timezone.utc)
        events = [
            {
                "type": "retry",
                "mode": "aggressive",
                "risk_classes": ["NARRATIVE"],
                "timestamp": _ts(0),
            }
            for _ in range(10)
        ]
        rate = compute_retry_rate("CODE", "aggressive", events, now=now)
        assert rate == pytest.approx(0.0)

    def test_old_events_dropped_after_30d(self):
        now = datetime.now(timezone.utc)
        events = [
            # 31 days old → weight=0, should be dropped
            {"type": "retry", "mode": "aggressive", "risk_classes": ["CODE"], "timestamp": _ts(31)}
            for _ in range(10)
        ]
        rate = compute_retry_rate("CODE", "aggressive", events, now=now)
        assert rate == pytest.approx(0.0)

    def test_7_14d_events_have_reduced_weight(self):
        now = datetime.now(timezone.utc)
        # 1 fresh retry + 1 fresh success → 50%
        # Replace fresh retry with 10d-old retry (weight=0.5): rate < 50%
        events = [
            {"type": "retry", "mode": "aggressive", "risk_classes": ["CODE"], "timestamp": _ts(10)},
            {"type": "success", "mode": "aggressive", "risk_classes": [], "timestamp": _ts(0)},
        ]
        rate = compute_retry_rate("CODE", "aggressive", events, now=now)
        # retry_w=0.5, success_w=1.0 → rate = 0.5/1.5 ≈ 0.333
        assert rate == pytest.approx(0.5 / 1.5, rel=1e-3)


# ---------------------------------------------------------------------------
# Auto-downgrade logic
# ---------------------------------------------------------------------------


class TestAutoDowngrade:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = make_path(self.tmpdir)

    def _flood(self, risk_class, mode, n_retry, n_success=0):
        for _ in range(n_retry):
            log_retry("q", mode, [risk_class], calibration_path=self.path)
        for _ in range(n_success):
            log_success("q", mode, calibration_path=self.path)

    def test_aggressive_above_threshold_downgrades_to_hybrid(self):
        # 8 retries, 2 successes → 80% retry rate > 20%
        self._flood("CODE", "aggressive", n_retry=8, n_success=2)
        data = load_calibration(self.path)
        assert data["overrides"].get("CODE") == "hybrid"

    def test_hybrid_above_threshold_downgrades_to_strict(self):
        self._flood("NARRATIVE", "hybrid", n_retry=8, n_success=2)
        data = load_calibration(self.path)
        assert data["overrides"].get("NARRATIVE") == "strict"

    def test_below_threshold_no_downgrade(self):
        # 1 retry, 9 successes → 10% < 20%
        self._flood("CODE", "aggressive", n_retry=1, n_success=9)
        data = load_calibration(self.path)
        assert "CODE" not in data["overrides"]

    def test_strict_never_downgrades_further(self):
        self._flood("LEGAL", "strict", n_retry=10, n_success=0)
        data = load_calibration(self.path)
        assert data["overrides"].get("LEGAL") is None  # Can't go below strict

    def test_override_persists_across_loads(self):
        self._flood("CODE", "aggressive", n_retry=8, n_success=2)
        # Reload from disk
        data2 = load_calibration(self.path)
        assert data2["overrides"].get("CODE") == "hybrid"

    def test_multiple_risk_classes_independent(self):
        self._flood("CODE", "aggressive", n_retry=8, n_success=2)
        self._flood("NARRATIVE", "aggressive", n_retry=1, n_success=9)
        data = load_calibration(self.path)
        assert data["overrides"].get("CODE") == "hybrid"
        assert "NARRATIVE" not in data["overrides"]


# ---------------------------------------------------------------------------
# get_effective_mode
# ---------------------------------------------------------------------------


class TestGetEffectiveMode:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = make_path(self.tmpdir)

    def test_no_override_returns_base_mode(self):
        mode = get_effective_mode("aggressive", "CODE", calibration_path=self.path)
        assert mode == "aggressive"

    def test_override_stricter_than_base_wins(self):
        # Override CODE → hybrid
        for _ in range(8):
            log_retry("q", "aggressive", ["CODE"], calibration_path=self.path)
        for _ in range(2):
            log_success("q", "aggressive", calibration_path=self.path)
        mode = get_effective_mode("aggressive", "CODE", calibration_path=self.path)
        assert mode == "hybrid"

    def test_base_mode_stricter_than_override_wins(self):
        # Manually write a hybrid override but caller requests strict
        data = load_calibration(self.path)
        data["overrides"]["CODE"] = "hybrid"
        from pathlib import Path

        Path(self.path).write_text(json.dumps(data))  # noqa: I001
        mode = get_effective_mode("strict", "CODE", calibration_path=self.path)
        assert mode == "strict"

    def test_unknown_risk_class_returns_base(self):
        mode = get_effective_mode("aggressive", "UNKNOWN_CLASS", calibration_path=self.path)
        assert mode == "aggressive"

    def test_case_insensitive_input(self):
        mode = get_effective_mode("AGGRESSIVE", "code", calibration_path=self.path)
        assert mode == "aggressive"

    def test_returns_string(self):
        mode = get_effective_mode("hybrid", "NARRATIVE", calibration_path=self.path)
        assert isinstance(mode, str)


# ---------------------------------------------------------------------------
# _downgrade_mode
# ---------------------------------------------------------------------------


class TestDowngradeMode:
    def test_aggressive_to_hybrid(self):
        assert _downgrade_mode("aggressive") == "hybrid"

    def test_hybrid_to_strict(self):
        assert _downgrade_mode("hybrid") == "strict"

    def test_strict_returns_none(self):
        assert _downgrade_mode("strict") is None

    def test_unknown_mode_returns_none(self):
        assert _downgrade_mode("turbo") is None


# ---------------------------------------------------------------------------
# Persistence: load_calibration
# ---------------------------------------------------------------------------


class TestPersistence:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = make_path(self.tmpdir)

    def test_missing_file_returns_empty_structure(self):
        data = load_calibration(self.path)
        assert data["overrides"] == {}
        assert data["events"] == []

    def test_corrupt_file_returns_empty_structure(self):
        from pathlib import Path

        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.path).write_text("{not valid json")
        data = load_calibration(self.path)
        assert data["overrides"] == {}

    def test_overrides_persist_across_restarts(self):
        log_retry("q", "aggressive", ["LEGAL"], calibration_path=self.path)
        for _ in range(9):
            log_retry("q", "aggressive", ["LEGAL"], calibration_path=self.path)
        # Simulate restart by calling load directly
        data = load_calibration(self.path)
        assert "LEGAL" in data["overrides"]

    def test_events_limited_to_max(self):
        for i in range(MAX_EVENTS + 10):
            log_success(f"q{i}", "aggressive", calibration_path=self.path)
        data = load_calibration(self.path)
        assert len(data["events"]) == MAX_EVENTS
