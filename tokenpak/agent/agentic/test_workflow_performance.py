"""Tests for workflow_performance module."""

import json
import tempfile
import time
from pathlib import Path

import pytest

from workflow import WorkflowRecord, WorkflowStatus
from workflow_performance import (
    WorkflowStats,
    WorkflowPerformanceTracker,
    record_workflow_execution,
)


class TestWorkflowStats:
    """Test WorkflowStats data class."""

    def test_success_rate_no_executions(self):
        """With no executions, default success rate is 0.5."""
        stats = WorkflowStats(template_name="test")
        assert stats.success_rate() == 0.5

    def test_success_rate_all_successful(self):
        """All successful executions yield 100% success rate."""
        stats = WorkflowStats(template_name="test", success_count=10, failure_count=0)
        assert stats.success_rate() == 1.0

    def test_success_rate_mixed(self):
        """Mixed executions yield correct success rate."""
        stats = WorkflowStats(template_name="test", success_count=7, failure_count=3)
        assert stats.success_rate() == 0.7

    def test_avg_duration_no_executions(self):
        """With no executions, average duration is infinity."""
        stats = WorkflowStats(template_name="test")
        assert stats.avg_duration_seconds() == float('inf')

    def test_avg_duration_with_executions(self):
        """Average duration is calculated correctly."""
        stats = WorkflowStats(
            template_name="test",
            success_count=5,
            failure_count=0,
            total_duration_seconds=50.0  # 5 executions, 10 seconds each
        )
        assert stats.avg_duration_seconds() == 10.0

    def test_avg_tokens_no_executions(self):
        """With no executions, average tokens is 0."""
        stats = WorkflowStats(template_name="test")
        assert stats.avg_tokens() == 0.0

    def test_avg_tokens_with_executions(self):
        """Average tokens is calculated correctly."""
        stats = WorkflowStats(
            template_name="test",
            success_count=4,
            failure_count=0,
            total_tokens=4000
        )
        assert stats.avg_tokens() == 1000.0

    def test_regression_rate(self):
        """Regression rate is calculated from count."""
        stats = WorkflowStats(
            template_name="test",
            success_count=8,
            failure_count=2,
            regression_count=1
        )
        assert stats.regression_rate() == 0.1  # 1 / 10


class TestWorkflowPerformanceTracker:
    """Test WorkflowPerformanceTracker."""

    @pytest.fixture
    def temp_stats_file(self):
        """Temporary stats file for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stats_file = Path(tmpdir) / "workflow_stats.json"
            yield stats_file

    def test_record_successful_execution(self, temp_stats_file):
        """Recording successful execution updates stats."""
        tracker = WorkflowPerformanceTracker(temp_stats_file)
        
        stats = tracker.record_execution(
            template_name="deploy",
            success=True,
            duration_seconds=15.5,
            tokens_used=1200,
            regression=False
        )
        
        assert stats.success_count == 1
        assert stats.failure_count == 0
        assert stats.total_duration_seconds == 15.5
        assert stats.total_tokens == 1200
        assert stats.regression_count == 0

    def test_record_failed_execution(self, temp_stats_file):
        """Recording failed execution updates stats."""
        tracker = WorkflowPerformanceTracker(temp_stats_file)
        
        stats = tracker.record_execution(
            template_name="deploy",
            success=False,
            duration_seconds=5.0,
            tokens_used=500,
            regression=False
        )
        
        assert stats.success_count == 0
        assert stats.failure_count == 1
        # Failed executions don't contribute to totals
        assert stats.total_duration_seconds == 0.0
        assert stats.total_tokens == 0

    def test_record_execution_with_regression(self, temp_stats_file):
        """Recording execution with regression increments regression count."""
        tracker = WorkflowPerformanceTracker(temp_stats_file)
        
        stats = tracker.record_execution(
            template_name="deploy",
            success=True,
            duration_seconds=10.0,
            tokens_used=800,
            regression=True
        )
        
        assert stats.regression_count == 1

    def test_multiple_executions_aggregate(self, temp_stats_file):
        """Multiple executions aggregate correctly."""
        tracker = WorkflowPerformanceTracker(temp_stats_file)
        
        # Three successful executions
        tracker.record_execution("deploy", success=True, duration_seconds=10.0, tokens_used=1000)
        tracker.record_execution("deploy", success=True, duration_seconds=20.0, tokens_used=2000)
        tracker.record_execution("deploy", success=True, duration_seconds=30.0, tokens_used=3000)
        # One failure (duration/tokens not added to totals)
        tracker.record_execution("deploy", success=False, duration_seconds=5.0, tokens_used=500)
        
        stats = tracker.get_stats("deploy")
        assert stats.success_count == 3
        assert stats.failure_count == 1
        assert stats.success_rate() == 0.75
        # avg_duration = total_duration / (success + failure) = 60 / 4 = 15.0
        assert stats.avg_duration_seconds() == 15.0
        # avg_tokens = total_tokens / (success + failure) = 6000 / 4 = 1500.0
        assert stats.avg_tokens() == 1500.0

    def test_persistence_across_instances(self, temp_stats_file):
        """Stats persist to disk and are loaded by new instances."""
        # Create tracker and record execution
        tracker1 = WorkflowPerformanceTracker(temp_stats_file)
        tracker1.record_execution("deploy", success=True, duration_seconds=10.0, tokens_used=1000)
        
        # Create new tracker instance with same file
        tracker2 = WorkflowPerformanceTracker(temp_stats_file)
        stats = tracker2.get_stats("deploy")
        
        assert stats is not None
        assert stats.success_count == 1
        assert stats.total_duration_seconds == 10.0

    def test_score_template_no_data(self, temp_stats_file):
        """Scoring a template with no data returns 0.0."""
        tracker = WorkflowPerformanceTracker(temp_stats_file)
        score = tracker.score_template("unknown_template")
        assert score == 0.0

    def test_score_template_perfect_execution(self, temp_stats_file):
        """Perfect execution (100% success, low duration/tokens) scores high."""
        tracker = WorkflowPerformanceTracker(temp_stats_file)
        
        # Perfect: 1 success, no failures, 10s duration, 1000 tokens
        tracker.record_execution(
            "deploy",
            success=True,
            duration_seconds=10.0,
            tokens_used=1000,
            regression=False
        )
        
        score = tracker.score_template("deploy", max_duration_seconds=300.0, max_tokens=100000)
        # success_rate=1.0 (0.5), speed=1.0-(10/300)=0.967 (0.2), token=1.0-(1000/100000)=0.99 (0.2), regression=1.0 (0.1)
        # score ≈ 1.0*0.5 + 0.967*0.2 + 0.99*0.2 + 1.0*0.1 ≈ 0.9588
        assert score > 0.95
        assert score < 1.0

    def test_score_template_poor_execution(self, temp_stats_file):
        """Poor execution (low success rate, regressions) scores moderately."""
        tracker = WorkflowPerformanceTracker(temp_stats_file)
        
        # Poor: 1 success, 3 failures, 250s duration, 80000 tokens, 1 regression
        tracker.record_execution("deploy", success=True, duration_seconds=250.0, tokens_used=80000, regression=True)
        tracker.record_execution("deploy", success=False, duration_seconds=100.0, tokens_used=50000)
        tracker.record_execution("deploy", success=False, duration_seconds=100.0, tokens_used=50000)
        tracker.record_execution("deploy", success=False, duration_seconds=100.0, tokens_used=50000)
        
        score = tracker.score_template("deploy", max_duration_seconds=300.0, max_tokens=100000)
        # success_rate=0.25, avg_duration=62.5/300≈0.208, speed=1-0.208=0.792
        # avg_tokens=20000/100000=0.2, token_eff=1-0.2=0.8, regression_rate=0.25, no_regr=0.75
        # score = 0.25*0.5 + 0.792*0.2 + 0.8*0.2 + 0.75*0.1 ≈ 0.5183
        assert 0.5 < score < 0.6

    def test_rank_templates(self, temp_stats_file):
        """Templates are ranked by score."""
        tracker = WorkflowPerformanceTracker(temp_stats_file)
        
        # Template A: good (success, fast, low tokens)
        for _ in range(5):
            tracker.record_execution("template_a", success=True, duration_seconds=10.0, tokens_used=1000)
        
        # Template B: poor (failures, slow, high tokens)
        tracker.record_execution("template_b", success=True, duration_seconds=200.0, tokens_used=80000)
        for _ in range(3):
            tracker.record_execution("template_b", success=False, duration_seconds=100.0, tokens_used=50000)
        
        # Template C: no data (should have score 0.0)
        # Don't record anything
        
        ranked = tracker.rank_templates("task", candidates=["template_a", "template_b", "template_c"])
        
        # template_a should be first (highest score)
        assert ranked[0][0] == "template_a"
        assert ranked[0][1] > ranked[1][1]
        # template_b should be second
        assert ranked[1][0] == "template_b"
        # template_c should be last (score 0.0)
        assert ranked[2][0] == "template_c"
        assert ranked[2][1] == 0.0

    def test_execution_history_capped(self, temp_stats_file):
        """Execution history is capped at 1000 entries."""
        tracker = WorkflowPerformanceTracker(temp_stats_file)
        
        # Record 1100 executions
        for i in range(1100):
            tracker.record_execution("deploy", success=True, duration_seconds=10.0, tokens_used=1000)
        
        stats = tracker.get_stats("deploy")
        # Should have exactly 1000 most recent executions
        assert len(stats.executions) == 1000

    def test_get_all_stats(self, temp_stats_file):
        """get_all_stats returns all templates."""
        tracker = WorkflowPerformanceTracker(temp_stats_file)
        
        tracker.record_execution("deploy", success=True, duration_seconds=10.0, tokens_used=1000)
        tracker.record_execution("refactor", success=True, duration_seconds=20.0, tokens_used=2000)
        
        all_stats = tracker.all_stats()
        assert len(all_stats) == 2
        assert "deploy" in all_stats
        assert "refactor" in all_stats

    def test_clear_stats_template(self, temp_stats_file):
        """clear_stats removes a specific template."""
        tracker = WorkflowPerformanceTracker(temp_stats_file)
        
        tracker.record_execution("deploy", success=True, duration_seconds=10.0, tokens_used=1000)
        tracker.record_execution("refactor", success=True, duration_seconds=20.0, tokens_used=2000)
        
        tracker.clear_stats("deploy")
        
        assert tracker.get_stats("deploy") is None
        assert tracker.get_stats("refactor") is not None

    def test_clear_all_stats(self, temp_stats_file):
        """clear_stats with no argument clears all templates."""
        tracker = WorkflowPerformanceTracker(temp_stats_file)
        
        tracker.record_execution("deploy", success=True, duration_seconds=10.0, tokens_used=1000)
        tracker.record_execution("refactor", success=True, duration_seconds=20.0, tokens_used=2000)
        
        tracker.clear_stats()
        
        assert tracker.all_stats() == {}


class TestWorkflowExecutionRecording:
    """Test convenience function for recording workflow executions."""

    @pytest.fixture
    def temp_stats_file(self):
        """Temporary stats file for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stats_file = Path(tmpdir) / "workflow_stats.json"
            yield stats_file

    def test_record_workflow_execution_success(self, temp_stats_file):
        """record_workflow_execution correctly records a successful workflow."""
        # Create a mock workflow
        workflow = WorkflowRecord(
            id="wf-123",
            name="test-workflow",
            template="deploy",
            steps=[],
            status=WorkflowStatus.COMPLETED,
            created_at=time.time(),
            started_at=time.time(),
            completed_at=time.time() + 10.0  # 10 second duration
        )
        
        tracker = WorkflowPerformanceTracker(temp_stats_file)
        record_workflow_execution(workflow, tokens_used=1500, regression=False, tracker=tracker)
        
        stats = tracker.get_stats("deploy")
        assert stats.success_count == 1
        assert stats.failure_count == 0
        assert stats.total_tokens == 1500

    def test_record_workflow_execution_failure(self, temp_stats_file):
        """record_workflow_execution correctly records a failed workflow."""
        workflow = WorkflowRecord(
            id="wf-124",
            name="test-workflow",
            template="deploy",
            steps=[],
            status=WorkflowStatus.FAILED,
            created_at=time.time(),
            started_at=time.time(),
            completed_at=time.time() + 5.0
        )
        
        tracker = WorkflowPerformanceTracker(temp_stats_file)
        record_workflow_execution(workflow, tokens_used=500, regression=False, tracker=tracker)
        
        stats = tracker.get_stats("deploy")
        assert stats.success_count == 0
        assert stats.failure_count == 1

    def test_record_workflow_execution_no_template(self, temp_stats_file):
        """record_workflow_execution skips workflows without a template."""
        workflow = WorkflowRecord(
            id="wf-125",
            name="test-workflow",
            template=None,  # No template
            steps=[],
            status=WorkflowStatus.COMPLETED,
            created_at=time.time(),
            started_at=time.time(),
            completed_at=time.time() + 10.0
        )
        
        tracker = WorkflowPerformanceTracker(temp_stats_file)
        record_workflow_execution(workflow, tokens_used=1000, tracker=tracker)
        
        # Should not record anything
        assert tracker.all_stats() == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
