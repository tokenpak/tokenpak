# SPDX-License-Identifier: MIT
"""Tests for tokenpak.agent.regression.stability_scorer."""

from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import pytest

from tokenpak.agent.regression.stability_scorer import (
    RunRecord,
    StabilityScore,
    StabilityScorer,
    _edit_distance_ratio,
    _normalise_token_volatility,
    compute_stability,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record(
    passed: bool = True,
    retried: bool = False,
    token_count: int = 1000,
    output_text: str = "output",
    validation_passed: bool = True,
) -> RunRecord:
    return RunRecord(
        timestamp="2026-03-10T00:00:00Z",
        passed=passed,
        retried=retried,
        token_count=token_count,
        output_text=output_text,
        validation_passed=validation_passed,
    )


# ---------------------------------------------------------------------------
# Test 1 — Perfect workflow → tight budget
# ---------------------------------------------------------------------------


def test_perfect_workflow_tight_budget():
    """100% pass, no retries, stable tokens → score > 0.8 → tight budget."""
    records = [_record(passed=True, retried=False, token_count=1000) for _ in range(10)]
    score = compute_stability("wf_perfect", records)

    assert score.score > 0.8, f"Expected score > 0.8, got {score.score}"
    assert score.budget_tier == "tight"
    assert math.isclose(score.budget_multiplier, 0.70)
    assert score.pass_rate == 1.0
    assert score.retry_rate == 0.0


# ---------------------------------------------------------------------------
# Test 2 — Chaotic workflow → expanded budget
# ---------------------------------------------------------------------------


def test_chaotic_workflow_expanded_budget():
    """Low pass rate, high retries, variable tokens → score < 0.5 → expanded."""
    records = [
        _record(
            passed=(i % 3 == 0),      # ~33% pass rate
            retried=True,
            token_count=500 + i * 200, # high variance
            validation_passed=(i % 4 == 0),
        )
        for i in range(12)
    ]
    score = compute_stability("wf_chaotic", records)

    assert score.score < 0.5, f"Expected score < 0.5, got {score.score}"
    assert score.budget_tier == "expanded"
    assert math.isclose(score.budget_multiplier, 1.30)


# ---------------------------------------------------------------------------
# Test 3 — Medium stability → normal budget
# ---------------------------------------------------------------------------


def test_medium_workflow_normal_budget():
    """Mixed results → 0.5 ≤ score ≤ 0.8 → normal budget."""
    records = [
        _record(
            passed=(i % 2 == 0),   # 50% pass rate
            retried=(i % 3 == 0),  # ~33% retry
            token_count=1000,      # stable tokens
            validation_passed=(i % 2 == 0),
        )
        for i in range(10)
    ]
    score = compute_stability("wf_medium", records)

    assert 0.5 <= score.score <= 0.8, f"Expected 0.5..0.8, got {score.score}"
    assert score.budget_tier == "normal"
    assert math.isclose(score.budget_multiplier, 1.00)


# ---------------------------------------------------------------------------
# Test 4 — No records → worst-case defaults
# ---------------------------------------------------------------------------


def test_empty_records_defaults():
    """Zero records → score 0.0, expanded budget, no crash."""
    score = compute_stability("wf_empty", [])

    assert score.score == 0.0
    assert score.budget_tier == "expanded"
    assert score.run_count == 0
    assert score.budget_multiplier == 1.30


# ---------------------------------------------------------------------------
# Test 5 — StabilityScorer persistence round-trip
# ---------------------------------------------------------------------------


def test_scorer_persistence_round_trip(tmp_path):
    """Records survive a save/reload cycle."""
    store = tmp_path / "stability_scores.json"
    scorer = StabilityScorer(store_path=str(store))

    for i in range(5):
        scorer.record_run("wf_persist", _record(passed=True, token_count=800 + i * 10))

    # Score and cache
    s1 = scorer.score_workflow("wf_persist")

    # Reload from disk
    scorer2 = StabilityScorer(store_path=str(store))
    s2 = scorer2.get_cached_score("wf_persist")

    assert s2 is not None
    assert s1.score == s2.score
    assert s1.workflow_id == s2.workflow_id
    assert len(scorer2.get_records("wf_persist")) == 5


# ---------------------------------------------------------------------------
# Test 6 — adjust_budget applies multiplier correctly
# ---------------------------------------------------------------------------


def test_adjust_budget():
    """Budget is multiplied by the stability-determined factor."""
    with tempfile.TemporaryDirectory() as td:
        scorer = StabilityScorer(store_path=str(Path(td) / "s.json"))
        # Seed with perfect records
        for _ in range(8):
            scorer.record_run("wf_budget", _record(passed=True, retried=False))
        scorer.score_workflow("wf_budget")

        adjusted, tier = scorer.adjust_budget("wf_budget", base_budget=10_000)
        assert tier == "tight"
        assert adjusted == math.ceil(10_000 * 0.70)


# ---------------------------------------------------------------------------
# Test 7 — edit_distance_ratio edge cases
# ---------------------------------------------------------------------------


def test_edit_distance_identical():
    assert _edit_distance_ratio("hello", "hello") == 0.0


def test_edit_distance_empty():
    assert _edit_distance_ratio("", "") == 0.0
    assert _edit_distance_ratio("abc", "") == 1.0
    assert _edit_distance_ratio("", "abc") == 1.0


# ---------------------------------------------------------------------------
# Test 8 — token_volatility_norm bounds
# ---------------------------------------------------------------------------


def test_token_volatility_norm_stable():
    """All same token count → zero volatility."""
    records = [_record(token_count=500) for _ in range(5)]
    assert _normalise_token_volatility(records) == 0.0


def test_token_volatility_norm_single():
    """Single record → zero (no stddev)."""
    assert _normalise_token_volatility([_record(token_count=1000)]) == 0.0


# ---------------------------------------------------------------------------
# Test 9 — score formula clamped to [0, 1]
# ---------------------------------------------------------------------------


def test_score_clamped():
    """Score must never exceed [0, 1] regardless of input extremes."""
    records = [_record() for _ in range(20)]
    score = compute_stability("wf_clamp", records)
    assert 0.0 <= score.score <= 1.0


# ---------------------------------------------------------------------------
# Test 10 — StabilityScore serialisation round-trip
# ---------------------------------------------------------------------------


def test_stability_score_serialisation():
    records = [_record(passed=True, retried=False) for _ in range(3)]
    score = compute_stability("wf_serial", records)
    d = score.to_dict()
    restored = StabilityScore.from_dict(d)
    assert restored.workflow_id == score.workflow_id
    assert restored.score == score.score
    assert restored.budget_tier == score.budget_tier
