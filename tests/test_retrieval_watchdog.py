# SPDX-License-Identifier: MIT
"""Tests for tokenpak._internal.regression.retrieval_watchdog."""

from __future__ import annotations

import pytest

pytest.importorskip("tokenpak._internal.regression.retrieval_watchdog", reason="module not available in current build")
import tempfile
from pathlib import Path
from typing import List, Optional

import pytest

from tokenpak._internal.regression.retrieval_watchdog import (
    QueryRetrievalRecord,
    RetrievalAlert,
    RetrievalQualityWatchdog,
    _rank_correlation,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    query_id: str = "q1",
    chunk_count: int = 10,
    unique_chunk_count: Optional[int] = None,
    relevance_scores: Optional[List[float]] = None,
    source_ids: Optional[List[str]] = None,
    chunk_ids_ordered: Optional[List[str]] = None,
) -> QueryRetrievalRecord:
    """Build a QueryRetrievalRecord with sensible defaults."""
    if unique_chunk_count is None:
        unique_chunk_count = chunk_count  # perfect dedup by default
    if relevance_scores is None:
        relevance_scores = [0.8] * chunk_count
    if source_ids is None:
        source_ids = [f"src_{i % 3}" for i in range(chunk_count)]
    if chunk_ids_ordered is None:
        chunk_ids_ordered = [f"c{i}" for i in range(chunk_count)]

    return QueryRetrievalRecord(
        query_id=query_id,
        query_text=f"test query {query_id}",
        chunk_count=chunk_count,
        unique_chunk_count=unique_chunk_count,
        relevance_scores=relevance_scores,
        source_ids=source_ids,
        chunk_ids_ordered=chunk_ids_ordered,
    )


def _watchdog_with_tmp(
    auto_remediate: bool = True,
    **kwargs,
) -> tuple[RetrievalQualityWatchdog, Path]:
    """Return a watchdog writing to a temp file."""
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    wd = RetrievalQualityWatchdog(
        history_path=tmp.name,
        auto_remediate=auto_remediate,
        **kwargs,
    )
    return wd, Path(tmp.name)


# ---------------------------------------------------------------------------
# Unit tests: QueryRetrievalRecord properties
# ---------------------------------------------------------------------------


class TestQueryRetrievalRecordProperties:
    def test_dedup_rate_perfect(self):
        r = _make_record(chunk_count=10, unique_chunk_count=10)
        assert r.dedup_rate == 1.0

    def test_dedup_rate_partial(self):
        r = _make_record(chunk_count=10, unique_chunk_count=8)
        assert r.dedup_rate == pytest.approx(0.8)

    def test_dedup_rate_zero_chunks(self):
        r = QueryRetrievalRecord(query_id="x", query_text="x", chunk_count=0)
        assert r.dedup_rate == 1.0

    def test_irrelevant_source_rate(self):
        scores = [0.9, 0.8, 0.2, 0.1, 0.7]  # 2 irrelevant
        r = _make_record(chunk_count=5, relevance_scores=scores)
        assert r.irrelevant_source_rate == pytest.approx(2 / 5)

    def test_source_diversity_all_unique(self):
        r = _make_record(chunk_count=4, source_ids=["a", "b", "c", "d"])
        assert r.source_diversity == 1.0

    def test_source_diversity_all_same(self):
        r = _make_record(chunk_count=4, source_ids=["a", "a", "a", "a"])
        assert r.source_diversity == pytest.approx(1 / 4)


# ---------------------------------------------------------------------------
# Unit tests: rank correlation helper
# ---------------------------------------------------------------------------


class TestRankCorrelation:
    def test_identical(self):
        ids = ["c1", "c2", "c3", "c4"]
        assert _rank_correlation(ids, ids) == 1.0

    def test_completely_different(self):
        a = ["c1", "c2", "c3"]
        b = ["c4", "c5", "c6"]
        assert _rank_correlation(a, b) == 0.0

    def test_empty_sequences(self):
        assert _rank_correlation([], []) == 1.0

    def test_partial_overlap(self):
        a = ["c1", "c2", "c3"]
        b = ["c3", "c1", "c4"]
        score = _rank_correlation(a, b)
        assert 0.0 < score < 1.0


# ---------------------------------------------------------------------------
# Test 1: No alert when within baseline
# ---------------------------------------------------------------------------


class TestNoAlertWithinBaseline:
    """No alert should fire when retrieval quality is stable."""

    def test_stable_stream_no_alert(self):
        wd, _ = _watchdog_with_tmp()

        # Seed baseline with 5 stable records
        for i in range(5):
            record = _make_record(
                query_id=f"q{i}",
                chunk_count=10,
                unique_chunk_count=10,
                relevance_scores=[0.8] * 10,
            )
            alert = wd.observe(record)
            assert alert is None, f"Unexpected alert on record {i}"

        # Observe another stable record — should be quiet
        stable = _make_record(
            query_id="q5",
            chunk_count=11,  # slight variation, well within threshold
            unique_chunk_count=11,
            relevance_scores=[0.82] * 11,
        )
        alert = wd.observe(stable)
        assert alert is None


# ---------------------------------------------------------------------------
# Test 2: Alert fires on chunk count growth
# ---------------------------------------------------------------------------


class TestChunkCountGrowthAlert:
    """Alert fires when chunk count grows more than 50% above baseline."""

    def test_chunk_count_growth_triggers_alert(self):
        wd, _ = _watchdog_with_tmp()

        # Establish a baseline of 10-chunk queries
        for i in range(5):
            wd.observe(_make_record(query_id=f"q{i}", chunk_count=10))

        # Now send a query returning 16 chunks (60% growth > 50% threshold)
        bloated = _make_record(query_id="q_bloated", chunk_count=16)
        alert = wd.observe(bloated)

        assert alert is not None
        assert "chunk_count_growth" in alert.dimensions
        assert alert.severity == "critical"

    def test_chunk_count_growth_remediation_tightens_filter(self):
        wd, _ = _watchdog_with_tmp(auto_remediate=True)
        for i in range(5):
            wd.observe(_make_record(query_id=f"q{i}", chunk_count=10))

        bloated = _make_record(query_id="q_bloated", chunk_count=16)
        alert = wd.observe(bloated)

        assert alert is not None
        assert alert.remediation_applied
        assert any("tighten_top_k_filter" in a for a in alert.remediation_actions)


# ---------------------------------------------------------------------------
# Test 3: Alert fires on high irrelevant source rate
# ---------------------------------------------------------------------------


class TestIrrelevantSourcesAlert:
    """Alert fires when >30% of chunks come from irrelevant sources."""

    def test_irrelevant_sources_triggers_alert(self):
        wd, _ = _watchdog_with_tmp()

        # Baseline: all highly relevant
        for i in range(5):
            wd.observe(
                _make_record(
                    query_id=f"q{i}",
                    chunk_count=10,
                    relevance_scores=[0.85] * 10,
                )
            )

        # 40% of chunks now irrelevant (score < 0.3)
        bad_scores = [0.9] * 6 + [0.1, 0.15, 0.2, 0.05]
        noisy = _make_record(
            query_id="q_noisy",
            chunk_count=10,
            relevance_scores=bad_scores,
        )
        alert = wd.observe(noisy)

        assert alert is not None
        assert "irrelevant_sources_high" in alert.dimensions

    def test_irrelevant_sources_remediation_triggers_reindex(self):
        wd, _ = _watchdog_with_tmp(auto_remediate=True)
        for i in range(5):
            wd.observe(
                _make_record(query_id=f"q{i}", chunk_count=10, relevance_scores=[0.85] * 10)
            )

        bad_scores = [0.9] * 6 + [0.1, 0.15, 0.2, 0.05]
        noisy = _make_record(query_id="q_noisy", chunk_count=10, relevance_scores=bad_scores)
        alert = wd.observe(noisy)

        assert alert is not None
        assert alert.remediation_applied
        assert any("trigger_reindex" in a for a in alert.remediation_actions)
        assert any("bm25" in a.lower() for a in alert.remediation_actions)


# ---------------------------------------------------------------------------
# Test 4: Alert fires on dedup rate drop
# ---------------------------------------------------------------------------


class TestDedupRateDropAlert:
    """Alert fires when dedup rate drops by more than 15 percentage points."""

    def test_dedup_rate_drop_triggers_alert(self):
        wd, _ = _watchdog_with_tmp()

        # Baseline: perfect dedup
        for i in range(5):
            wd.observe(_make_record(query_id=f"q{i}", chunk_count=10, unique_chunk_count=10))

        # Only 7 of 12 chunks are unique → dedup rate 0.583 (vs baseline 1.0 — drop > 0.15)
        dup_heavy = _make_record(query_id="q_dup", chunk_count=12, unique_chunk_count=7)
        alert = wd.observe(dup_heavy)

        assert alert is not None
        assert "dedup_rate_drop" in alert.dimensions

    def test_dedup_rate_drop_remediation_strengthens_dedup(self):
        wd, _ = _watchdog_with_tmp(auto_remediate=True)
        for i in range(5):
            wd.observe(_make_record(query_id=f"q{i}", chunk_count=10, unique_chunk_count=10))

        dup_heavy = _make_record(query_id="q_dup", chunk_count=12, unique_chunk_count=7)
        alert = wd.observe(dup_heavy)

        assert alert is not None
        assert alert.remediation_applied
        assert any("dedup" in a for a in alert.remediation_actions)


# ---------------------------------------------------------------------------
# Test 5: History persistence
# ---------------------------------------------------------------------------


class TestHistoryPersistence:
    """History survives a watchdog restart via JSON file."""

    def test_history_persists_across_restarts(self):
        wd, tmp_path = _watchdog_with_tmp()

        for i in range(5):
            wd.observe(_make_record(query_id=f"q{i}", chunk_count=10))

        # Create a second watchdog pointing at the same file
        wd2 = RetrievalQualityWatchdog(history_path=str(tmp_path))
        baseline = wd2.get_baseline()

        assert baseline.sample_size == 5
        assert baseline.mean_chunk_count == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Test 6: Custom remediation callback
# ---------------------------------------------------------------------------


class TestCustomRemediationCallback:
    """Custom remediation_fn is called instead of default logic."""

    def test_custom_remediation_fn_invoked(self):
        called_with = []

        def my_remediation(alert: RetrievalAlert):
            called_with.append(alert)
            return ["custom_action: cleared cache"]

        wd, _ = _watchdog_with_tmp(auto_remediate=False, remediation_fn=my_remediation)
        for i in range(5):
            wd.observe(_make_record(query_id=f"q{i}", chunk_count=10))

        bloated = _make_record(query_id="q_bloated", chunk_count=16)
        alert = wd.observe(bloated)

        assert alert is not None
        assert len(called_with) == 1
        assert alert.remediation_applied
        assert "custom_action: cleared cache" in alert.remediation_actions


# ---------------------------------------------------------------------------
# Test 7: Below baseline window — no false alerts
# ---------------------------------------------------------------------------


class TestBelowBaselineWindow:
    """No alerts should fire until at least 3 baseline records are collected."""

    def test_no_alert_with_fewer_than_3_samples(self):
        wd, _ = _watchdog_with_tmp()

        # Only 2 records in baseline — shouldn't alert even on wild outlier
        wd.observe(_make_record(query_id="q0", chunk_count=10))
        wd.observe(_make_record(query_id="q1", chunk_count=10))

        outlier = _make_record(query_id="q_outlier", chunk_count=100)
        alert = wd.observe(outlier)

        # Should be None because we only have 2 samples (< 3 minimum)
        assert alert is None
