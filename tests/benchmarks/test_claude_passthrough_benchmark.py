"""Contract tests for the Claude Code passthrough latency gate."""

from __future__ import annotations

import dataclasses
import json

import pytest

from scripts.benchmark_claude_passthrough import (
    BASELINE_PATH,
    MAX_REGRESSION_PERCENT,
    NORMALIZATION_METHOD,
    BenchmarkResult,
    _normalize_p50,
    compare_result,
    load_baseline,
    run_benchmark,
)


def _result(normalized_p50: float) -> BenchmarkResult:
    baseline = load_baseline()
    return BenchmarkResult(
        scenario=baseline["scenario"],
        samples=baseline["samples"],
        warmup_samples=baseline["warmup_samples"],
        iterations_per_sample=baseline["iterations_per_sample"],
        measurement_rounds=baseline["measurement_rounds"],
        payload_bytes=baseline["payload_bytes"],
        raw_p50_ns=100.0,
        raw_p95_ns=110.0,
        calibration_p50_ns=20.0,
        normalized_p50=normalized_p50,
        python="3.12",
        machine="test",
    )


def test_committed_baseline_has_fixed_five_percent_limit():
    baseline = load_baseline()
    assert baseline["max_regression_percent"] == MAX_REGRESSION_PERCENT == 5.0
    assert baseline["normalized_p50"] > 0
    assert baseline["raw_p50_ns"] > 0
    assert baseline["calibration_p50_ns"] > 0


def test_comparator_passes_at_exact_boundary():
    baseline = load_baseline()
    boundary = baseline["normalized_p50"] * 1.05
    comparison = compare_result(_result(boundary), baseline)
    assert comparison.passed is True


def test_comparator_fails_above_five_percent():
    baseline = load_baseline()
    regressed = baseline["normalized_p50"] * 1.050001
    comparison = compare_result(_result(regressed), baseline)
    assert comparison.passed is False
    assert "regressed" in comparison.reason


def test_normalization_uses_independent_medians_not_pairwise_ratios():
    target_samples = [10.0, 20.0, 30.0]
    calibration_samples = [2.0, 10.0, 10.0]
    assert _normalize_p50(target_samples, calibration_samples) == 2.0


def test_comparator_fails_closed_on_contract_drift():
    baseline = load_baseline()
    drifted = dataclasses.replace(_result(baseline["normalized_p50"]), payload_bytes=1)
    comparison = compare_result(drifted, baseline)
    assert comparison.passed is False
    assert "contract drift" in comparison.reason


def test_loader_rejects_threshold_weakening(tmp_path):
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    baseline["max_regression_percent"] = 5.1
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(baseline), encoding="utf-8")
    with pytest.raises(ValueError, match="fixed five-percent"):
        load_baseline(path)


def test_loader_rejects_normalization_drift(tmp_path):
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    baseline["normalization_method"] = NORMALIZATION_METHOD + "-weakened"
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(baseline), encoding="utf-8")
    with pytest.raises(ValueError, match="normalization mismatch"):
        load_baseline(path)


def test_loader_rejects_capture_median_tampering(tmp_path):
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    baseline["normalized_p50"] *= 2
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(baseline), encoding="utf-8")
    with pytest.raises(ValueError, match="capture median"):
        load_baseline(path)


def test_loader_rejects_nonpositive_capture(tmp_path):
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    baseline["normalized_p50_captures"][0] = 0
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(baseline), encoding="utf-8")
    with pytest.raises(ValueError, match="must be positive"):
        load_baseline(path)


def test_real_scenario_is_offline_and_byte_preserved():
    result = run_benchmark(
        samples=3, warmup_samples=1, iterations_per_sample=1, measurement_rounds=1
    )
    assert result.raw_p50_ns > 0
    assert result.normalized_p50 > 0
    assert result.payload_bytes == load_baseline()["payload_bytes"]
