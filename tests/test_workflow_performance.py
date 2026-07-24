"""Tests for tokenpak.workflow_performance.

Coverage:
- WorkflowStats: computed properties (success_rate, avg_duration, avg_tokens,
  regression_rate) and serialisation round-trip.
- WorkflowPerformanceTracker: record(), persist/reload, score_template(),
  rank_templates(), history capping, edge cases.
- record_workflow_execution() convenience wrapper.
"""

from __future__ import annotations

import pytest

pytest.importorskip("tokenpak.workflow_performance", reason="module not available in current build")
import time
from unittest.mock import MagicMock

import pytest
from tokenpak.workflow_performance import (
    MAX_HISTORY,
    WorkflowPerformanceTracker,
    WorkflowStats,
    record_workflow_execution,
)

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_stats(tmp_path):
    """Return a fresh tracker backed by a temp file."""
    return WorkflowPerformanceTracker(stats_path=tmp_path / "stats.json")


# ── WorkflowStats unit tests ─────────────────────────────────────────────────


class TestWorkflowStats:
    def test_success_rate_no_data(self):
        s = WorkflowStats(template="deploy")
        assert s.success_rate == 0.0

    def test_success_rate_all_success(self):
        s = WorkflowStats(template="deploy", success_count=10, failure_count=0)
        assert s.success_rate == 1.0

    def test_success_rate_mixed(self):
        s = WorkflowStats(template="deploy", success_count=3, failure_count=1)
        assert s.success_rate == pytest.approx(0.75)

    def test_avg_duration_no_data(self):
        s = WorkflowStats(template="deploy")
        assert s.avg_duration == 0.0

    def test_avg_duration_computed(self):
        s = WorkflowStats(
            template="deploy", success_count=2, failure_count=0, total_duration_seconds=60.0
        )
        assert s.avg_duration == pytest.approx(30.0)

    def test_avg_tokens_computed(self):
        s = WorkflowStats(template="deploy", success_count=4, failure_count=0, total_tokens=8000)
        assert s.avg_tokens == pytest.approx(2000.0)

    def test_regression_rate_no_successes(self):
        s = WorkflowStats(template="deploy", success_count=0, regression_count=0)
        assert s.regression_rate == 0.0

    def test_regression_rate_computed(self):
        s = WorkflowStats(template="deploy", success_count=5, regression_count=1)
        assert s.regression_rate == pytest.approx(0.2)

    def test_serialisation_round_trip(self):
        s = WorkflowStats(
            template="proxy",
            success_count=3,
            failure_count=1,
            total_duration_seconds=45.5,
            total_tokens=12000,
            regression_count=1,
            history=[
                {"ts": 1.0, "success": True, "duration": 10.0, "tokens": 3000, "regression": False}
            ],
        )
        restored = WorkflowStats.from_dict(s.to_dict())
        assert restored.template == s.template
        assert restored.success_count == s.success_count
        assert restored.failure_count == s.failure_count
        assert restored.total_duration_seconds == s.total_duration_seconds
        assert restored.total_tokens == s.total_tokens
        assert restored.regression_count == s.regression_count
        assert restored.history == s.history


# ── WorkflowPerformanceTracker tests ─────────────────────────────────────────


class TestWorkflowPerformanceTracker:
    def test_record_success_increments_counters(self, tmp_stats):
        tmp_stats.record("deploy", success=True, duration_seconds=10.0, tokens_used=500)
        s = tmp_stats.get_stats("deploy")
        assert s.success_count == 1
        assert s.failure_count == 0
        assert s.total_duration_seconds == pytest.approx(10.0)
        assert s.total_tokens == 500

    def test_record_failure_increments_counters(self, tmp_stats):
        tmp_stats.record("deploy", success=False, duration_seconds=5.0)
        s = tmp_stats.get_stats("deploy")
        assert s.failure_count == 1
        assert s.success_count == 0

    def test_record_regression_only_on_success(self, tmp_stats):
        tmp_stats.record("deploy", success=True, duration_seconds=5.0, regression=True)
        tmp_stats.record("deploy", success=False, duration_seconds=5.0, regression=True)
        s = tmp_stats.get_stats("deploy")
        assert s.regression_count == 1  # only the successful run counts

    def test_persistence_across_instances(self, tmp_path):
        path = tmp_path / "stats.json"
        t1 = WorkflowPerformanceTracker(stats_path=path)
        t1.record("proxy", success=True, duration_seconds=20.0, tokens_used=2000)

        t2 = WorkflowPerformanceTracker(stats_path=path)
        s = t2.get_stats("proxy")
        assert s is not None
        assert s.success_count == 1
        assert s.total_tokens == 2000

    def test_history_appended(self, tmp_stats):
        tmp_stats.record("deploy", success=True, duration_seconds=10.0)
        tmp_stats.record("deploy", success=False, duration_seconds=5.0)
        s = tmp_stats.get_stats("deploy")
        assert len(s.history) == 2
        assert s.history[0]["success"] is True
        assert s.history[1]["success"] is False

    def test_history_capped_at_max(self, tmp_stats):
        for _ in range(MAX_HISTORY + 50):
            tmp_stats.record("deploy", success=True, duration_seconds=1.0)
        s = tmp_stats.get_stats("deploy")
        assert len(s.history) == MAX_HISTORY

    def test_score_no_data_returns_zero(self, tmp_stats):
        assert tmp_stats.score_template("unknown") == 0.0

    def test_score_perfect_run(self, tmp_stats):
        # Very fast, very few tokens, always succeeds, no regressions
        tmp_stats.record("proxy", success=True, duration_seconds=1.0, tokens_used=100)
        score = tmp_stats.score_template("proxy", max_duration=300.0, max_tokens=50_000)
        # success_rate=1.0, speed ≈ 0.997, token_eff ≈ 0.998, no_regression=1.0
        assert score > 0.9

    def test_score_poor_run(self, tmp_stats):
        # Slow, token-heavy, failed
        tmp_stats.record("deploy", success=False, duration_seconds=290.0, tokens_used=49_000)
        score = tmp_stats.score_template("deploy", max_duration=300.0, max_tokens=50_000)
        assert score < 0.4

    def test_score_formula_components(self, tmp_stats):
        """Manually verify each weighted component."""
        tmp_stats.record(
            "deploy", success=True, duration_seconds=150.0, tokens_used=25_000, regression=False
        )
        s = tmp_stats.get_stats("deploy")
        expected = (
            s.success_rate * 0.5
            + max(0.0, 1.0 - s.avg_duration / 300.0) * 0.2
            + max(0.0, 1.0 - s.avg_tokens / 50_000) * 0.2
            + (1.0 - s.regression_rate) * 0.1
        )
        assert tmp_stats.score_template("deploy") == pytest.approx(expected)

    def test_rank_templates_order(self, tmp_stats):
        # "proxy" should beat "deploy" — faster, fewer tokens, same success rate
        tmp_stats.record("deploy", success=True, duration_seconds=200.0, tokens_used=20_000)
        tmp_stats.record("proxy", success=True, duration_seconds=10.0, tokens_used=1_000)
        ranked = tmp_stats.rank_templates("code-gen", candidates=["deploy", "proxy"])
        assert ranked[0][0] == "proxy"
        assert ranked[1][0] == "deploy"

    def test_rank_templates_candidates_filter(self, tmp_stats):
        tmp_stats.record("deploy", success=True, duration_seconds=50.0)
        tmp_stats.record("proxy", success=True, duration_seconds=50.0)
        tmp_stats.record("release", success=True, duration_seconds=50.0)
        ranked = tmp_stats.rank_templates("x", candidates=["deploy", "release"])
        names = [n for n, _ in ranked]
        assert "proxy" not in names
        assert set(names) == {"deploy", "release"}

    def test_rank_all_no_data_scores_zero(self, tmp_stats):
        ranked = tmp_stats.rank_templates("any", candidates=["a", "b", "c"])
        scores = [sc for _, sc in ranked]
        assert all(sc == 0.0 for sc in scores)

    def test_all_stats_returns_copy(self, tmp_stats):
        tmp_stats.record("deploy", success=True, duration_seconds=5.0)
        all_s = tmp_stats.all_stats()
        all_s["deploy"].success_count = 999  # mutate copy
        assert tmp_stats.get_stats("deploy").success_count == 1  # original unchanged


# ── record_workflow_execution tests ──────────────────────────────────────────


class TestRecordWorkflowExecution:
    def _make_wf(self, template, status_value, started_at, completed_at):
        from tokenpak.agentic.workflow import WorkflowRecord, WorkflowStatus

        wf = MagicMock(spec=WorkflowRecord)
        wf.template = template
        wf.status = WorkflowStatus(status_value)
        wf.started_at = started_at
        wf.completed_at = completed_at
        wf.duration_seconds.return_value = (
            round(completed_at - started_at, 3) if started_at and completed_at else 0.0
        )
        return wf

    def test_records_successful_workflow(self, tmp_stats):
        now = time.time()
        wf = self._make_wf("proxy", "completed", now - 30, now)
        result = record_workflow_execution(wf, tokens_used=1000, tracker=tmp_stats)
        assert result is not None
        assert result.success_count == 1
        assert result.total_tokens == 1000

    def test_records_failed_workflow(self, tmp_stats):
        now = time.time()
        wf = self._make_wf("deploy", "failed", now - 10, now)
        result = record_workflow_execution(wf, tokens_used=200, tracker=tmp_stats)
        assert result.failure_count == 1

    def test_no_template_returns_none(self, tmp_stats):
        wf = MagicMock()
        wf.template = None
        result = record_workflow_execution(wf, tracker=tmp_stats)
        assert result is None
