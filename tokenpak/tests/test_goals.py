"""Unit tests for tokenpak.cli.goals module.

Tests cover:
- Dataclasses: GoalProgress, Goal
- Enums: GoalType, GoalStatus
- GoalManager class: CRUD operations, progress tracking, milestone detection, pace alerts
- Edge cases: empty goals, boundary conditions, state persistence
"""

import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from tokenpak.cli.goals import (
    Goal,
    GoalManager,
    GoalProgress,
    GoalStatus,
    GoalType,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_goals_dir():
    """Temporary directory for goal files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def goal_manager(temp_goals_dir):
    """GoalManager with temp directory."""
    goals_path = Path(temp_goals_dir) / "goals.yaml"
    state_path = Path(temp_goals_dir) / "goal_state.json"
    return GoalManager(goals_path=str(goals_path), state_path=str(state_path))


@pytest.fixture
def sample_goal():
    """Sample Goal object."""
    return Goal(
        goal_id="goal_001",
        name="Monthly Savings Target",
        goal_type="savings",
        target_value=500.0,
        start_date="2026-03-01",
        end_date="2026-03-31",
        description="Reduce monthly AI costs by 20%",
    )


# ---------------------------------------------------------------------------
# Test: Enums
# ---------------------------------------------------------------------------


def test_goal_type_enum():
    """Test GoalType enum values."""
    assert GoalType.SAVINGS.value == "savings"
    assert GoalType.COMPRESSION.value == "compression"
    assert GoalType.CACHE.value == "cache"
    assert GoalType.METRIC.value == "metric"


def test_goal_status_enum():
    """Test GoalStatus enum values."""
    assert GoalStatus.ACTIVE.value == "active"
    assert GoalStatus.COMPLETED.value == "completed"
    assert GoalStatus.PAUSED.value == "paused"
    assert GoalStatus.BEHIND.value == "behind"
    assert GoalStatus.ON_TRACK.value == "on_track"
    assert GoalStatus.AHEAD.value == "ahead"


# ---------------------------------------------------------------------------
# Test: GoalProgress dataclass
# ---------------------------------------------------------------------------


def test_goal_progress_construction():
    """Test GoalProgress dataclass creation."""
    progress = GoalProgress(
        goal_id="goal_001",
        current_value=50.0,
        target_value=100.0,
        progress_percent=50.0,
    )

    assert progress.goal_id == "goal_001"
    assert progress.current_value == 50.0
    assert progress.target_value == 100.0
    assert progress.progress_percent == 50.0
    assert progress.milestone_25_fired is False
    assert progress.milestone_50_fired is False
    assert progress.pace_status == "on_track"


def test_goal_progress_to_dict():
    """Test GoalProgress serialization."""
    progress = GoalProgress(goal_id="goal_001", current_value=50.0, target_value=100.0)
    data = progress.to_dict()

    assert isinstance(data, dict)
    assert data["goal_id"] == "goal_001"
    assert data["current_value"] == 50.0


def test_goal_progress_from_dict():
    """Test GoalProgress deserialization."""
    data = {
        "goal_id": "goal_001",
        "current_value": 50.0,
        "target_value": 100.0,
        "progress_percent": 50.0,
        "milestone_25_fired": True,
        "milestone_50_fired": False,
        "milestone_75_fired": False,
        "milestone_100_fired": False,
        "pace_status": "on_track",
        "pace_alert_fired": False,
        "last_update": 1234567890.0,
    }

    progress = GoalProgress.from_dict(data)
    assert progress.goal_id == "goal_001"
    assert progress.milestone_25_fired is True
    assert progress.current_value == 50.0


# ---------------------------------------------------------------------------
# Test: Goal dataclass
# ---------------------------------------------------------------------------


def test_goal_construction(sample_goal):
    """Test Goal dataclass creation."""
    assert sample_goal.goal_id == "goal_001"
    assert sample_goal.name == "Monthly Savings Target"
    assert sample_goal.goal_type == "savings"
    assert sample_goal.target_value == 500.0
    assert sample_goal.enabled is True


def test_goal_days_remaining(sample_goal):
    """Test Goal.days_remaining() calculation."""
    # Set end_date to 10 days in the future
    future = (date.today() + timedelta(days=10)).isoformat()
    sample_goal.end_date = future

    remaining = sample_goal.days_remaining()
    assert 9 <= remaining <= 10


def test_goal_days_elapsed(sample_goal):
    """Test Goal.days_elapsed() calculation."""
    # Set start_date to 5 days ago
    past = (date.today() - timedelta(days=5)).isoformat()
    sample_goal.start_date = past

    elapsed = sample_goal.days_elapsed()
    assert elapsed == 5


def test_goal_total_days(sample_goal):
    """Test Goal.total_days() calculation."""
    sample_goal.start_date = "2026-03-01"
    sample_goal.end_date = "2026-03-31"

    total = sample_goal.total_days()
    assert total == 30


def test_goal_expected_progress_percent():
    """Test Goal.expected_progress_percent() based on elapsed time."""
    # 30-day goal, half elapsed
    goal = Goal(
        goal_id="test",
        name="Test",
        goal_type="savings",
        target_value=100.0,
        start_date=(date.today() - timedelta(days=15)).isoformat(),
        end_date=(date.today() + timedelta(days=15)).isoformat(),
    )

    expected = goal.expected_progress_percent()
    # Should be around 50% (15 days elapsed out of ~30 total)
    assert 45 <= expected <= 55


def test_goal_to_dict(sample_goal):
    """Test Goal serialization."""
    data = sample_goal.to_dict()

    assert isinstance(data, dict)
    assert data["goal_id"] == "goal_001"
    assert data["name"] == "Monthly Savings Target"


def test_goal_from_dict():
    """Test Goal deserialization."""
    data = {
        "goal_id": "goal_001",
        "name": "Test Goal",
        "goal_type": "savings",
        "target_value": 100.0,
        "start_date": "2026-03-01",
        "end_date": "2026-03-31",
    }

    goal = Goal.from_dict(data)
    assert goal.goal_id == "goal_001"
    assert goal.name == "Test Goal"


# ---------------------------------------------------------------------------
# Test 1: GoalManager.add_goal()
# ---------------------------------------------------------------------------


def test_goal_manager_add_goal(goal_manager):
    """Test 1: GoalManager.add_goal() creates and persists a goal."""
    goal = goal_manager.add_goal(
        name="Test Savings Goal",
        goal_type="savings",
        target_value=1000.0,
        description="Save $1000 on API calls",
    )

    assert goal.goal_id is not None
    assert goal.name == "Test Savings Goal"
    assert goal.goal_type == "savings"
    assert goal.target_value == 1000.0

    # Verify it was added to manager
    assert goal.goal_id in goal_manager.goals
    assert goal.goal_id in goal_manager.progress


# ---------------------------------------------------------------------------
# Test 2: GoalManager.get_goal() and get_progress()
# ---------------------------------------------------------------------------


def test_goal_manager_get_goal(goal_manager):
    """Test 2: GoalManager.get_goal() and get_progress() retrieve stored data."""
    created = goal_manager.add_goal(
        name="Retrieve Test",
        goal_type="compression",
        target_value=50.0,
    )

    retrieved = goal_manager.get_goal(created.goal_id)
    assert retrieved is not None
    assert retrieved.name == "Retrieve Test"

    progress = goal_manager.get_progress(created.goal_id)
    assert progress is not None
    assert progress.goal_id == created.goal_id
    assert progress.target_value == 50.0


# ---------------------------------------------------------------------------
# Test 3: GoalManager.edit_goal()
# ---------------------------------------------------------------------------


def test_goal_manager_edit_goal(goal_manager):
    """Test 3: GoalManager.edit_goal() updates goal fields."""
    goal = goal_manager.add_goal(
        name="Original Name",
        goal_type="savings",
        target_value=100.0,
    )

    updated = goal_manager.edit_goal(goal.goal_id, name="Updated Name", target_value=200.0)

    assert updated is not None
    assert updated.name == "Updated Name"
    assert updated.target_value == 200.0

    # Verify progress target was updated
    progress = goal_manager.get_progress(goal.goal_id)
    assert progress.target_value == 200.0


def test_goal_manager_edit_goal_not_found(goal_manager):
    """Test 3b: GoalManager.edit_goal() returns None for non-existent goal."""
    result = goal_manager.edit_goal("nonexistent", name="New Name")
    assert result is None


# ---------------------------------------------------------------------------
# Test 4: GoalManager.delete_goal()
# ---------------------------------------------------------------------------


def test_goal_manager_delete_goal(goal_manager):
    """Test 4: GoalManager.delete_goal() removes goal and progress."""
    goal = goal_manager.add_goal(
        name="To Delete",
        goal_type="savings",
        target_value=100.0,
    )
    goal_id = goal.goal_id

    result = goal_manager.delete_goal(goal_id)

    assert result is True
    assert goal_id not in goal_manager.goals
    assert goal_id not in goal_manager.progress


def test_goal_manager_delete_goal_not_found(goal_manager):
    """Test 4b: GoalManager.delete_goal() returns False for non-existent goal."""
    result = goal_manager.delete_goal("nonexistent")
    assert result is False


# ---------------------------------------------------------------------------
# Test 5: GoalManager.update_progress()
# ---------------------------------------------------------------------------


def test_goal_manager_update_progress(goal_manager):
    """Test 5: GoalManager.update_progress() updates progress and pace status."""
    goal = goal_manager.add_goal(
        name="Progress Test",
        goal_type="savings",
        target_value=100.0,
        start_date=(date.today() - timedelta(days=15)).isoformat(),
        end_date=(date.today() + timedelta(days=15)).isoformat(),
    )

    progress = goal_manager.update_progress(goal.goal_id, current_value=60.0)

    assert progress is not None
    assert progress.current_value == 60.0
    assert progress.progress_percent == 60.0
    # Should be on track (60% progress, ~50% time elapsed)
    assert progress.pace_status in ["on_track", "ahead"]


def test_goal_manager_update_progress_ahead(goal_manager):
    """Test pace status is 'ahead' when actual > expected + tolerance."""
    goal = goal_manager.add_goal(
        name="Ahead Test",
        goal_type="savings",
        target_value=100.0,
        start_date=(date.today() - timedelta(days=1)).isoformat(),
        end_date=(date.today() + timedelta(days=29)).isoformat(),
    )

    # After 1 day, only ~3% time elapsed, but we're at 50% progress
    progress = goal_manager.update_progress(goal.goal_id, current_value=50.0)

    assert progress.pace_status == "ahead"


def test_goal_manager_update_progress_behind(goal_manager):
    """Test pace status is 'behind' when actual < expected - tolerance."""
    goal = goal_manager.add_goal(
        name="Behind Test",
        goal_type="savings",
        target_value=100.0,
        start_date=(date.today() - timedelta(days=25)).isoformat(),
        end_date=(date.today() + timedelta(days=5)).isoformat(),
    )

    # After 25 days, ~83% time elapsed, but only at 10% progress
    progress = goal_manager.update_progress(goal.goal_id, current_value=10.0)

    assert progress.pace_status == "behind"


# ---------------------------------------------------------------------------
# Test 6: GoalManager.list_goals()
# ---------------------------------------------------------------------------


def test_goal_manager_list_goals(goal_manager):
    """Test 6: GoalManager.list_goals() returns all goals."""
    goal1 = goal_manager.add_goal("Goal 1", "savings", 100.0)
    goal2 = goal_manager.add_goal("Goal 2", "compression", 50.0)
    goal3 = goal_manager.add_goal("Goal 3", "cache", 75.0)

    all_goals = goal_manager.list_goals()
    assert len(all_goals) == 3


def test_goal_manager_list_goals_by_type(goal_manager):
    """Test 6b: GoalManager.list_goals() filters by goal_type."""
    goal_manager.add_goal("Savings 1", "savings", 100.0)
    goal_manager.add_goal("Savings 2", "savings", 200.0)
    goal_manager.add_goal("Compression 1", "compression", 50.0)

    # list_goals should filter by goal_type
    compression_goals = goal_manager.list_goals(goal_type="compression")
    assert len(compression_goals) == 1
    assert all(g.goal_type == "compression" for g in compression_goals)


def test_goal_manager_list_goals_by_status(goal_manager):
    """Test 6c: GoalManager.list_goals() filters by status (completed/active)."""
    goal1 = goal_manager.add_goal("Completed Goal", "savings", 100.0)
    goal2 = goal_manager.add_goal("Active Goal", "savings", 100.0)
    goal3 = goal_manager.add_goal("Another Active", "savings", 100.0)

    # Mark goal1 as completed
    goal_manager.update_progress(goal1.goal_id, 100.0)

    # Partially progress the others
    goal_manager.update_progress(goal2.goal_id, 50.0)
    goal_manager.update_progress(goal3.goal_id, 75.0)

    # Get all first to verify baseline
    all_goals = goal_manager.list_goals()
    assert len(all_goals) == 3

    completed = goal_manager.list_goals(status="completed")
    assert len(completed) == 1
    assert completed[0].goal_id == goal1.goal_id

    active = goal_manager.list_goals(status="active")
    assert len(active) == 2  # goal2 and goal3


# ---------------------------------------------------------------------------
# Test 7: GoalManager.check_milestones()
# ---------------------------------------------------------------------------


def test_goal_manager_check_milestones(goal_manager):
    """Test 7: GoalManager.check_milestones() fires milestone events."""
    goal = goal_manager.add_goal("Milestone Test", "savings", 100.0)

    # Update to 25%
    goal_manager.update_progress(goal.goal_id, 25.0)
    events = goal_manager.check_milestones(goal.goal_id)

    assert len(events) == 1
    assert events[0]["milestone"] == 25
    assert "25%" in events[0]["message"]

    # Update to 50% (should trigger 50% milestone)
    goal_manager.update_progress(goal.goal_id, 50.0)
    events = goal_manager.check_milestones(goal.goal_id)

    assert len(events) == 1
    assert events[0]["milestone"] == 50


def test_goal_manager_check_milestones_multiple(goal_manager):
    """Test milestones fire in order when jumping progress."""
    goal = goal_manager.add_goal("Jump Test", "savings", 100.0)

    # Jump straight to 75%
    goal_manager.update_progress(goal.goal_id, 75.0)
    events = goal_manager.check_milestones(goal.goal_id)

    # Should fire 25%, 50%, 75% all at once
    assert len(events) >= 3
    milestones = [e["milestone"] for e in events]
    assert 25 in milestones
    assert 50 in milestones
    assert 75 in milestones


def test_goal_manager_check_milestones_no_duplicates(goal_manager):
    """Test milestones don't fire twice."""
    goal = goal_manager.add_goal("No Dup Test", "savings", 100.0)

    # Fire 25% milestone
    goal_manager.update_progress(goal.goal_id, 25.0)
    events1 = goal_manager.check_milestones(goal.goal_id)

    # Check again without progress change
    events2 = goal_manager.check_milestones(goal.goal_id)

    assert len(events1) == 1
    assert len(events2) == 0  # No duplicate


# ---------------------------------------------------------------------------
# Test 8: GoalManager.check_pace_alerts()
# ---------------------------------------------------------------------------


def test_goal_manager_check_pace_alerts_behind(goal_manager):
    """Test 8: GoalManager.check_pace_alerts() fires when behind schedule."""
    goal = goal_manager.add_goal(
        "Pace Test",
        "savings",
        100.0,
        start_date=(date.today() - timedelta(days=25)).isoformat(),
        end_date=(date.today() + timedelta(days=5)).isoformat(),
    )

    # 83% time elapsed, but only 10% progress (behind)
    goal_manager.update_progress(goal.goal_id, 10.0)

    alert = goal_manager.check_pace_alerts(goal.goal_id)

    assert alert is not None
    assert alert["type"] == "pace"
    assert alert["status"] == "behind"
    assert "⚠️" in alert["message"]


def test_goal_manager_check_pace_alerts_no_alert_if_on_track(goal_manager):
    """Test pace alert doesn't fire when on track."""
    goal = goal_manager.add_goal(
        "On Track Test",
        "savings",
        100.0,
        start_date=(date.today() - timedelta(days=15)).isoformat(),
        end_date=(date.today() + timedelta(days=15)).isoformat(),
    )

    # On track: ~50% time, 50% progress
    goal_manager.update_progress(goal.goal_id, 50.0)

    alert = goal_manager.check_pace_alerts(goal.goal_id)

    assert alert is None


def test_goal_manager_check_pace_alerts_no_duplicate(goal_manager):
    """Test pace alert doesn't fire twice."""
    goal = goal_manager.add_goal(
        "Dup Test",
        "savings",
        100.0,
        start_date=(date.today() - timedelta(days=25)).isoformat(),
        end_date=(date.today() + timedelta(days=5)).isoformat(),
    )

    # First check: alert fires
    goal_manager.update_progress(goal.goal_id, 10.0)
    alert1 = goal_manager.check_pace_alerts(goal.goal_id)

    # Second check: no duplicate
    alert2 = goal_manager.check_pace_alerts(goal.goal_id)

    assert alert1 is not None
    assert alert2 is None


# ---------------------------------------------------------------------------
# Test 9: GoalManager.get_summary_stats()
# ---------------------------------------------------------------------------


def test_goal_manager_get_summary_stats_empty(goal_manager):
    """Test 9: GoalManager.get_summary_stats() with no goals."""
    stats = goal_manager.get_summary_stats()

    assert stats["total_goals"] == 0
    assert stats["active_goals"] == 0
    assert stats["completed_goals"] == 0
    assert stats["avg_progress"] == 0.0


def test_goal_manager_get_summary_stats_with_goals(goal_manager):
    """Test 9b: GoalManager.get_summary_stats() calculates averages."""
    goal1 = goal_manager.add_goal("Goal 1", "savings", 100.0)
    goal2 = goal_manager.add_goal("Goal 2", "savings", 100.0)
    goal3 = goal_manager.add_goal("Goal 3", "savings", 100.0)

    goal_manager.update_progress(goal1.goal_id, 25.0)  # 25%
    goal_manager.update_progress(goal2.goal_id, 50.0)  # 50%
    goal_manager.update_progress(goal3.goal_id, 100.0)  # 100%

    stats = goal_manager.get_summary_stats()

    assert stats["total_goals"] == 3
    assert stats["completed_goals"] == 1  # goal3
    assert stats["active_goals"] == 2  # goal1, goal2
    assert stats["avg_progress"] == (25.0 + 50.0 + 100.0) / 3


# ---------------------------------------------------------------------------
# Test 10: Persistence (state loading and saving)
# ---------------------------------------------------------------------------


def test_goal_manager_persistence(temp_goals_dir):
    """Test 10: GoalManager persists and loads state from disk."""
    # Create manager and add goals
    gm1 = GoalManager(
        goals_path=f"{temp_goals_dir}/goals.yaml",
        state_path=f"{temp_goals_dir}/goal_state.json",
    )

    goal = gm1.add_goal("Persistent Goal", "savings", 500.0)
    gm1.update_progress(goal.goal_id, 250.0)

    # Create new manager instance (should load from disk)
    gm2 = GoalManager(
        goals_path=f"{temp_goals_dir}/goals.yaml",
        state_path=f"{temp_goals_dir}/goal_state.json",
    )

    # Verify goal was loaded
    loaded_goal = gm2.get_goal(goal.goal_id)
    assert loaded_goal is not None
    assert loaded_goal.name == "Persistent Goal"

    loaded_progress = gm2.get_progress(goal.goal_id)
    assert loaded_progress is not None
    assert loaded_progress.current_value == 250.0


# ---------------------------------------------------------------------------
# Test 11: Custom metric goals
# ---------------------------------------------------------------------------


def test_goal_manager_custom_metric_goal(goal_manager):
    """Test 11: GoalManager supports custom metric goals."""
    goal = goal_manager.add_goal(
        name="Cache Hit Rate Goal",
        goal_type="metric",
        target_value=85.0,
        metric_name="cache_hit_rate",
    )

    assert goal.goal_type == "metric"
    assert goal.metric_name == "cache_hit_rate"

    # Update with custom metric value
    progress = goal_manager.update_progress(goal.goal_id, 72.5)

    assert progress.progress_percent == (72.5 / 85.0) * 100


# ---------------------------------------------------------------------------
# Test 12: Rolling window goals
# ---------------------------------------------------------------------------


def test_goal_manager_rolling_window_goal(goal_manager):
    """Test 12: GoalManager supports rolling window (weekly pace) goals."""
    goal = goal_manager.add_goal(
        name="Weekly Pace Goal",
        goal_type="savings",
        target_value=100.0,
        rolling_window=True,
    )

    assert goal.rolling_window is True

    # Verify it's stored and retrievable
    retrieved = goal_manager.get_goal(goal.goal_id)
    assert retrieved.rolling_window is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
