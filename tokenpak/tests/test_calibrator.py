# SPDX-License-Identifier: Apache-2.0
"""Unit tests for tokenpak.calibrator — Compression Calibration."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tokenpak.routing.calibrator import (
    DECAY_7D_WEIGHT,
    DECAY_14D_WEIGHT,
    DECAY_30D_CUTOFF,
    MAX_EVENTS,
    _age_days,
    _downgrade_mode,
    _event_weight,
    _load,
    _parse_ts,
    _recompute_overrides,
    _save,
    compute_retry_rate,
    get_effective_mode,
    load_calibration,
    log_retry,
    log_success,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cal_path(tmp_path):
    """Return a temp path for calibration.json."""
    return str(tmp_path / "calibration.json")


def _make_event(ev_type: str, mode: str, risk_classes=None, age_hours: float = 1.0) -> dict:
    """Build a synthetic event dict with a known age."""
    ts = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat()
    return {
        "type": ev_type,
        "query": "test query",
        "mode": mode.lower(),
        "risk_classes": risk_classes or [],
        "timestamp": ts,
    }


def _retry(mode: str, risk_classes, age_hours: float = 1.0):
    return _make_event("retry", mode, risk_classes, age_hours)


def _success(mode: str, age_hours: float = 1.0):
    return _make_event("success", mode, [], age_hours)


# ---------------------------------------------------------------------------
# _parse_ts
# ---------------------------------------------------------------------------


class TestParseTs:
    def test_valid_iso_utc(self):
        ts = "2026-01-15T10:00:00+00:00"
        dt = _parse_ts(ts)
        assert dt is not None
        assert dt.tzinfo is not None

    def test_naive_becomes_utc(self):
        ts = "2026-01-15T10:00:00"
        dt = _parse_ts(ts)
        assert dt is not None
        assert dt.tzinfo == timezone.utc

    def test_invalid_string_returns_none(self):
        assert _parse_ts("not-a-date") is None
        assert _parse_ts("") is None
        assert _parse_ts(None) is None  # type: ignore


# ---------------------------------------------------------------------------
# _age_days
# ---------------------------------------------------------------------------


class TestAgeDays:
    def test_recent_event(self):
        now = datetime.now(timezone.utc)
        ts = (now - timedelta(hours=12)).isoformat()
        age = _age_days(ts, now)
        assert age is not None
        assert pytest.approx(age, abs=0.1) == 0.5

    def test_one_week_old(self):
        now = datetime.now(timezone.utc)
        ts = (now - timedelta(days=7)).isoformat()
        age = _age_days(ts, now)
        assert age is not None
        assert pytest.approx(age, abs=0.1) == 7.0

    def test_invalid_ts_returns_none(self):
        now = datetime.now(timezone.utc)
        assert _age_days("bad", now) is None


# ---------------------------------------------------------------------------
# _event_weight
# ---------------------------------------------------------------------------


class TestEventWeight:
    def test_recent_event_weight_1(self):
        assert _event_weight(0.5) == 1.0
        assert _event_weight(6.9) == 1.0

    def test_8_day_event_half_weight(self):
        assert _event_weight(8.0) == DECAY_7D_WEIGHT

    def test_15_day_event_quarter_weight(self):
        assert _event_weight(15.0) == DECAY_14D_WEIGHT

    def test_expired_event_zero_weight(self):
        assert _event_weight(31.0) == 0.0
        assert _event_weight(DECAY_30D_CUTOFF + 1) == 0.0

    def test_boundary_exactly_30_days(self):
        # exactly 30 days: still within cutoff (> not >=)
        assert _event_weight(float(DECAY_30D_CUTOFF)) == DECAY_14D_WEIGHT


# ---------------------------------------------------------------------------
# _downgrade_mode
# ---------------------------------------------------------------------------


class TestDowngradeMode:
    def test_aggressive_downgrades_to_hybrid(self):
        assert _downgrade_mode("aggressive") == "hybrid"

    def test_hybrid_downgrades_to_strict(self):
        assert _downgrade_mode("hybrid") == "strict"

    def test_strict_returns_none(self):
        assert _downgrade_mode("strict") is None

    def test_uppercase_input(self):
        assert _downgrade_mode("AGGRESSIVE") == "hybrid"

    def test_unknown_returns_none(self):
        assert _downgrade_mode("ultrafast") is None


# ---------------------------------------------------------------------------
# _load / _save
# ---------------------------------------------------------------------------


class TestLoadSave:
    def test_load_missing_file_returns_fresh(self, cal_path):
        data = _load(cal_path)
        assert data == {"overrides": {}, "events": [], "updated": data["updated"]}

    def test_load_corrupt_file_returns_fresh(self, tmp_path):
        p = tmp_path / "calibration.json"
        p.write_text("{ not valid json")
        data = _load(str(p))
        assert data["overrides"] == {}
        assert data["events"] == []

    def test_save_then_load_roundtrip(self, cal_path):
        data = {"overrides": {"CODE": "strict"}, "events": [], "updated": "2026-01-01T00:00:00"}
        _save(data, cal_path)
        loaded = _load(cal_path)
        assert loaded["overrides"] == {"CODE": "strict"}

    def test_save_creates_parent_dirs(self, tmp_path):
        nested = str(tmp_path / "deep" / "nested" / "cal.json")
        _save({"overrides": {}, "events": []}, nested)
        assert Path(nested).exists()

    def test_load_sets_missing_keys(self, tmp_path):
        p = tmp_path / "minimal.json"
        p.write_text(json.dumps({"overrides": {}}))  # missing events + updated
        data = _load(str(p))
        assert "events" in data
        assert "updated" in data


# ---------------------------------------------------------------------------
# compute_retry_rate
# ---------------------------------------------------------------------------


class TestComputeRetryRate:
    def test_no_events_returns_zero(self):
        rate = compute_retry_rate("CODE", "aggressive", [])
        assert rate == 0.0

    def test_all_retries_returns_one(self):
        events = [_retry("aggressive", ["CODE"]) for _ in range(5)]
        rate = compute_retry_rate("CODE", "aggressive", events)
        assert pytest.approx(rate, abs=0.01) == 1.0

    def test_all_successes_returns_zero(self):
        events = [_success("aggressive") for _ in range(5)]
        rate = compute_retry_rate("CODE", "aggressive", events)
        assert rate == 0.0

    def test_mixed_events(self):
        # 2 retries + 8 successes = 20% retry rate
        events = [_retry("aggressive", ["CODE"]) for _ in range(2)]
        events += [_success("aggressive") for _ in range(8)]
        rate = compute_retry_rate("CODE", "aggressive", events)
        assert pytest.approx(rate, abs=0.02) == 0.20

    def test_ignores_different_mode(self):
        events = [_retry("hybrid", ["CODE"]) for _ in range(5)]
        rate = compute_retry_rate("CODE", "aggressive", events)
        assert rate == 0.0

    def test_ignores_different_risk_class(self):
        events = [_retry("aggressive", ["NARRATIVE"]) for _ in range(5)]
        rate = compute_retry_rate("CODE", "aggressive", events)
        assert rate == 0.0

    def test_expired_events_excluded(self):
        events = [_retry("aggressive", ["CODE"], age_hours=32 * 24) for _ in range(10)]
        rate = compute_retry_rate("CODE", "aggressive", events)
        assert rate == 0.0

    def test_old_events_downweighted(self):
        # 5 recent retries (weight 1.0) + 5 old retries (weight 0.25)
        # vs 5 recent successes (weight 1.0)
        recent_retries = [_retry("aggressive", ["CODE"], age_hours=1) for _ in range(5)]
        old_retries = [_retry("aggressive", ["CODE"], age_hours=16 * 24) for _ in range(5)]
        successes = [_success("aggressive", age_hours=1) for _ in range(5)]
        events = recent_retries + old_retries + successes
        rate = compute_retry_rate("CODE", "aggressive", events)
        # retry_w = 5*1.0 (recent) + 5*0.25 (old, >14d)
        # success_w = 5*1.0 (recent successes)
        # rate = retry_w / (retry_w + success_w)
        retry_w = 5 * 1.0 + 5 * DECAY_14D_WEIGHT
        success_w = 5 * 1.0
        expected = retry_w / (retry_w + success_w)
        assert pytest.approx(rate, abs=0.02) == expected

    def test_case_insensitive_risk_class(self):
        events = [_retry("aggressive", ["code"]) for _ in range(3)]
        events += [_success("aggressive") for _ in range(7)]
        rate_upper = compute_retry_rate("CODE", "aggressive", events)
        rate_lower = compute_retry_rate("code", "aggressive", events)
        assert pytest.approx(rate_upper, abs=0.01) == pytest.approx(rate_lower, abs=0.01)


# ---------------------------------------------------------------------------
# _recompute_overrides
# ---------------------------------------------------------------------------


class TestRecomputeOverrides:
    def test_no_retries_no_overrides(self):
        data = {"overrides": {}, "events": [_success("aggressive") for _ in range(10)]}
        _recompute_overrides(data)
        assert data["overrides"] == {}

    def test_high_retry_rate_triggers_override(self):
        # 5 retries + 1 success = 83% retry rate → override
        events = [_retry("aggressive", ["CODE"]) for _ in range(5)]
        events += [_success("aggressive")]
        data = {"overrides": {}, "events": events}
        _recompute_overrides(data)
        assert "CODE" in data["overrides"]
        assert data["overrides"]["CODE"] == "hybrid"

    def test_below_threshold_no_override(self):
        # 1 retry + 9 successes = 10% → no override
        events = [_retry("aggressive", ["NARRATIVE"])]
        events += [_success("aggressive") for _ in range(9)]
        data = {"overrides": {}, "events": events}
        _recompute_overrides(data)
        assert "NARRATIVE" not in data["overrides"]

    def test_override_cleared_when_rate_recovers(self):
        # Start with override set manually
        data = {"overrides": {"CODE": "hybrid"}, "events": []}
        # Now add many successes with no retries
        data["events"] = [_success("aggressive") for _ in range(20)]
        _recompute_overrides(data)
        # Override should be cleared
        assert "CODE" not in data["overrides"]

    def test_hybrid_mode_retry_upgrades_to_strict(self):
        events = [_retry("hybrid", ["CODE"]) for _ in range(5)]
        events += [_success("hybrid")]
        data = {"overrides": {}, "events": events}
        _recompute_overrides(data)
        assert data["overrides"].get("CODE") == "strict"


# ---------------------------------------------------------------------------
# log_retry
# ---------------------------------------------------------------------------


class TestLogRetry:
    def test_adds_retry_event(self, cal_path):
        data = log_retry("test query", "aggressive", ["CODE"], cal_path)
        retries = [e for e in data["events"] if e["type"] == "retry"]
        assert len(retries) == 1
        assert retries[0]["mode"] == "aggressive"
        assert "CODE" in retries[0]["risk_classes"]

    def test_persists_to_file(self, cal_path):
        log_retry("q", "aggressive", ["NARRATIVE"], cal_path)
        loaded = _load(cal_path)
        assert len(loaded["events"]) == 1

    def test_trims_to_max_events(self, cal_path):
        # Pre-fill with MAX_EVENTS events
        data = _load(cal_path)
        data["events"] = [_success("hybrid") for _ in range(MAX_EVENTS)]
        _save(data, cal_path)
        # Add one more — should trim to MAX_EVENTS
        result = log_retry("q", "aggressive", ["CODE"], cal_path)
        assert len(result["events"]) == MAX_EVENTS

    def test_triggers_override_at_threshold(self, cal_path):
        # Push retry rate above 20% for CODE/aggressive
        for _ in range(5):
            log_retry("q", "aggressive", ["CODE"], cal_path)
        log_success("q", "aggressive", cal_path)
        data = _load(cal_path)
        assert "CODE" in data["overrides"]

    def test_risk_classes_uppercased(self, cal_path):
        data = log_retry("q", "aggressive", ["code", "narrative"], cal_path)
        rc = data["events"][-1]["risk_classes"]
        assert "CODE" in rc
        assert "NARRATIVE" in rc


# ---------------------------------------------------------------------------
# log_success
# ---------------------------------------------------------------------------


class TestLogSuccess:
    def test_adds_success_event(self, cal_path):
        data = log_success("q", "hybrid", cal_path)
        successes = [e for e in data["events"] if e["type"] == "success"]
        assert len(successes) == 1
        assert successes[0]["mode"] == "hybrid"

    def test_success_clears_stale_override(self, cal_path):
        # First log retries to set override
        for _ in range(5):
            log_retry("q", "aggressive", ["CODE"], cal_path)

        data = _load(cal_path)
        assert "CODE" in data["overrides"]

        # Now flood with successes to clear it
        for _ in range(50):
            log_success("q", "aggressive", cal_path)

        data = _load(cal_path)
        assert "CODE" not in data["overrides"]

    def test_persists_to_file(self, cal_path):
        log_success("q", "strict", cal_path)
        loaded = _load(cal_path)
        assert len(loaded["events"]) == 1


# ---------------------------------------------------------------------------
# get_effective_mode
# ---------------------------------------------------------------------------


class TestGetEffectiveMode:
    def test_no_override_returns_base_mode(self, cal_path):
        assert get_effective_mode("aggressive", "CODE", cal_path) == "aggressive"

    def test_override_stricter_than_base_wins(self, cal_path):
        # Create an override for CODE → hybrid
        data = _load(cal_path)
        data["overrides"]["CODE"] = "hybrid"
        _save(data, cal_path)
        result = get_effective_mode("aggressive", "CODE", cal_path)
        assert result == "hybrid"

    def test_base_already_stricter_than_override(self, cal_path):
        # Base = strict, override = hybrid → strict wins (higher index)
        data = _load(cal_path)
        data["overrides"]["CODE"] = "hybrid"
        _save(data, cal_path)
        result = get_effective_mode("strict", "CODE", cal_path)
        assert result == "strict"

    def test_unknown_risk_class_returns_base(self, cal_path):
        data = _load(cal_path)
        data["overrides"]["CODE"] = "hybrid"
        _save(data, cal_path)
        result = get_effective_mode("aggressive", "NARRATIVE", cal_path)
        assert result == "aggressive"

    def test_case_insensitive_risk_class(self, cal_path):
        data = _load(cal_path)
        data["overrides"]["CODE"] = "hybrid"
        _save(data, cal_path)
        assert get_effective_mode("aggressive", "code", cal_path) == "hybrid"
        assert get_effective_mode("aggressive", "CODE", cal_path) == "hybrid"

    def test_end_to_end_override_from_retries(self, cal_path):
        # Log enough retries to trigger override, then verify effective mode changes
        for _ in range(5):
            log_retry("q", "aggressive", ["CODE"], cal_path)
        log_success("q", "aggressive", cal_path)  # keep rate at ~83%
        result = get_effective_mode("aggressive", "CODE", cal_path)
        assert result != "aggressive"  # should be downgraded


# ---------------------------------------------------------------------------
# load_calibration
# ---------------------------------------------------------------------------


class TestLoadCalibration:
    def test_returns_dict(self, cal_path):
        result = load_calibration(cal_path)
        assert isinstance(result, dict)
        assert "overrides" in result
        assert "events" in result

    def test_reflects_written_data(self, cal_path):
        log_retry("q", "aggressive", ["NARRATIVE"], cal_path)
        result = load_calibration(cal_path)
        assert len(result["events"]) == 1
