# SPDX-License-Identifier: MIT
"""Tests for TokenPak Goals — Savings Goals and Progress Tracking

Comprehensive test suite covering:
- Goal creation, editing, deletion
- Progress tracking and calculations
- Milestone alerts (25%, 50%, 75%, 100%)
- Pace alerts (on_track, ahead, behind)
- Goal state persistence
- Custom metrics support
- Rolling window goals
"""

import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from tokenpak.goals import (
    Goal,
    GoalManager,
    GoalProgress,
    GoalType,
    GoalStatus,
)


class TestGoal(unittest.TestCase):
    """Test Goal class and calculations."""

    def test_goal_creation(self):
        """Test creating a goal with basic fields."""
        goal = Goal(
            goal_id="test_1",
            name="Q1 Savings",
            goal_type="savings",
            target_value=100.0,
            start_date="2026-03-01",
            end_date="2026-03-31",
        )
        
        self.assertEqual(goal.goal_id, "test_1")
        self.assertEqual(goal.name, "Q1 Savings")
        self.assertEqual(goal.goal_type, "savings")
        self.assertEqual(goal.target_value, 100.0)

    def test_goal_days_calculation(self):
        """Test goal elapsed/remaining days calculation."""
        today = datetime.now().date()
        start = today.isoformat()
        end = (today + timedelta(days=30)).isoformat()
        
        goal = Goal(
            goal_id="test_2",
            name="Test Goal",
            goal_type="savings",
            target_value=50.0,
            start_date=start,
            end_date=end,
        )
        
        # Days elapsed should be 0 (started today)
        self.assertEqual(goal.days_elapsed(), 0)
        
        # Days remaining should be 30
        self.assertEqual(goal.days_remaining(), 30)
        
        # Total days should be 30
        self.assertEqual(goal.total_days(), 30)

    def test_expected_progress(self):
        """Test expected progress calculation based on elapsed time."""
        # Create goal from 10 days ago to 20 days from now (30 days total)
        today = datetime.now().date()
        start = (today - timedelta(days=10)).isoformat()
        end = (today + timedelta(days=20)).isoformat()
        
        goal = Goal(
            goal_id="test_3",
            name="Mid-Progress Goal",
            goal_type="savings",
            target_value=100.0,
            start_date=start,
            end_date=end,
        )
        
        # Should be approximately 33% (10/30)
        expected = goal.expected_progress_percent()
        self.assertAlmostEqual(expected, 33.33, delta=1.0)

    def test_goal_to_dict(self):
        """Test serialization to dict."""
        goal = Goal(
            goal_id="test_4",
            name="Serialization Test",
            goal_type="compression",
            target_value=80.0,
            start_date="2026-03-01",
            end_date="2026-03-31",
            description="Test compression goal",
        )
        
        data = goal.to_dict()
        self.assertEqual(data["goal_id"], "test_4")
        self.assertEqual(data["name"], "Serialization Test")
        self.assertEqual(data["goal_type"], "compression")
        self.assertEqual(data["target_value"], 80.0)

    def test_goal_from_dict(self):
        """Test deserialization from dict."""
        data = {
            "goal_id": "test_5",
            "name": "Deserialization Test",
            "goal_type": "cache",
            "target_value": 90.0,
            "start_date": "2026-03-01",
            "end_date": "2026-03-31",
            "description": "Test cache goal",
            "metric_name": "",
            "rolling_window": False,
            "enabled": True,
            "created_at": 1710000000.0,
            "metadata": {},
        }
        
        goal = Goal.from_dict(data)
        self.assertEqual(goal.goal_id, "test_5")
        self.assertEqual(goal.name, "Deserialization Test")
        self.assertEqual(goal.goal_type, "cache")


class TestGoalProgress(unittest.TestCase):
    """Test GoalProgress tracking."""

    def test_progress_creation(self):
        """Test creating a progress tracker."""
        progress = GoalProgress(
            goal_id="test_1",
            current_value=25.0,
            target_value=100.0,
        )
        
        self.assertEqual(progress.goal_id, "test_1")
        self.assertEqual(progress.current_value, 25.0)
        self.assertEqual(progress.target_value, 100.0)

    def test_progress_to_dict(self):
        """Test progress serialization."""
        progress = GoalProgress(
            goal_id="test_2",
            current_value=50.0,
            target_value=100.0,
            progress_percent=50.0,
        )
        
        data = progress.to_dict()
        self.assertIn("goal_id", data)
        self.assertEqual(data["current_value"], 50.0)
        self.assertEqual(data["progress_percent"], 50.0)

    def test_progress_from_dict(self):
        """Test progress deserialization."""
        data = {
            "goal_id": "test_3",
            "current_value": 75.0,
            "target_value": 100.0,
            "progress_percent": 75.0,
            "milestone_25_fired": True,
            "milestone_50_fired": True,
            "milestone_75_fired": False,
            "milestone_100_fired": False,
            "pace_status": "ahead",
            "pace_alert_fired": False,
            "last_update": 1710000000.0,
        }
        
        progress = GoalProgress.from_dict(data)
        self.assertEqual(progress.goal_id, "test_3")
        self.assertEqual(progress.current_value, 75.0)
        self.assertTrue(progress.milestone_50_fired)
        self.assertEqual(progress.pace_status, "ahead")


class TestGoalManager(unittest.TestCase):
    """Test GoalManager functionality."""

    def setUp(self):
        """Set up temporary directory for tests."""
        self.temp_dir = TemporaryDirectory()
        self.goals_path = Path(self.temp_dir.name) / "goals.yaml"
        self.state_path = Path(self.temp_dir.name) / "goal_state.json"

    def tearDown(self):
        """Clean up temporary directory."""
        self.temp_dir.cleanup()

    def test_manager_creation(self):
        """Test creating a GoalManager."""
        manager = GoalManager(
            goals_path=str(self.goals_path),
            state_path=str(self.state_path),
        )
        
        self.assertEqual(len(manager.goals), 0)
        self.assertEqual(len(manager.progress), 0)

    def test_add_goal(self):
        """Test adding a goal."""
        manager = GoalManager(
            goals_path=str(self.goals_path),
            state_path=str(self.state_path),
        )
        
        goal = manager.add_goal(
            name="Test Savings",
            goal_type="savings",
            target_value=100.0,
            description="Test goal",
        )
        
        self.assertIsNotNone(goal.goal_id)
        self.assertEqual(goal.name, "Test Savings")
        self.assertEqual(len(manager.goals), 1)
        self.assertIn(goal.goal_id, manager.progress)

    def test_add_multiple_goals(self):
        """Test adding multiple goals."""
        manager = GoalManager(
            goals_path=str(self.goals_path),
            state_path=str(self.state_path),
        )
        
        goal1 = manager.add_goal(
            name="Goal 1",
            goal_type="savings",
            target_value=50.0,
        )
        
        goal2 = manager.add_goal(
            name="Goal 2",
            goal_type="compression",
            target_value=80.0,
        )
        
        self.assertEqual(len(manager.goals), 2)
        self.assertNotEqual(goal1.goal_id, goal2.goal_id)

    def test_edit_goal(self):
        """Test editing an existing goal."""
        manager = GoalManager(
            goals_path=str(self.goals_path),
            state_path=str(self.state_path),
        )
        
        goal = manager.add_goal(
            name="Original Name",
            goal_type="savings",
            target_value=100.0,
        )
        
        edited = manager.edit_goal(goal.goal_id, name="Updated Name", target_value=150.0)
        
        self.assertIsNotNone(edited)
        self.assertEqual(edited.name, "Updated Name")
        self.assertEqual(edited.target_value, 150.0)

    def test_edit_nonexistent_goal(self):
        """Test editing a goal that doesn't exist."""
        manager = GoalManager(
            goals_path=str(self.goals_path),
            state_path=str(self.state_path),
        )
        
        result = manager.edit_goal("nonexistent_id", name="Test")
        self.assertIsNone(result)

    def test_delete_goal(self):
        """Test deleting a goal."""
        manager = GoalManager(
            goals_path=str(self.goals_path),
            state_path=str(self.state_path),
        )
        
        goal = manager.add_goal(
            name="Delete Me",
            goal_type="savings",
            target_value=50.0,
        )
        
        self.assertEqual(len(manager.goals), 1)
        
        deleted = manager.delete_goal(goal.goal_id)
        self.assertTrue(deleted)
        self.assertEqual(len(manager.goals), 0)
        self.assertNotIn(goal.goal_id, manager.progress)

    def test_delete_nonexistent_goal(self):
        """Test deleting a goal that doesn't exist."""
        manager = GoalManager(
            goals_path=str(self.goals_path),
            state_path=str(self.state_path),
        )
        
        result = manager.delete_goal("nonexistent_id")
        self.assertFalse(result)

    def test_get_goal(self):
        """Test retrieving a goal."""
        manager = GoalManager(
            goals_path=str(self.goals_path),
            state_path=str(self.state_path),
        )
        
        goal = manager.add_goal(
            name="Retrievable Goal",
            goal_type="savings",
            target_value=75.0,
        )
        
        retrieved = manager.get_goal(goal.goal_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.name, "Retrievable Goal")

    def test_update_progress(self):
        """Test updating goal progress."""
        manager = GoalManager(
            goals_path=str(self.goals_path),
            state_path=str(self.state_path),
        )
        
        goal = manager.add_goal(
            name="Progress Test",
            goal_type="savings",
            target_value=100.0,
        )
        
        # Update to 50%
        progress = manager.update_progress(goal.goal_id, 50.0)
        
        self.assertIsNotNone(progress)
        self.assertEqual(progress.current_value, 50.0)
        self.assertAlmostEqual(progress.progress_percent, 50.0, places=1)

    def test_progress_percent_calculation(self):
        """Test progress percentage calculation."""
        manager = GoalManager(
            goals_path=str(self.goals_path),
            state_path=str(self.state_path),
        )
        
        goal = manager.add_goal(
            name="Percent Test",
            goal_type="savings",
            target_value=200.0,
        )
        
        progress = manager.update_progress(goal.goal_id, 150.0)
        self.assertAlmostEqual(progress.progress_percent, 75.0, places=1)

    def test_list_goals(self):
        """Test listing goals."""
        manager = GoalManager(
            goals_path=str(self.goals_path),
            state_path=str(self.state_path),
        )
        
        manager.add_goal(name="Goal 1", goal_type="savings", target_value=50.0)
        manager.add_goal(name="Goal 2", goal_type="compression", target_value=80.0)
        manager.add_goal(name="Goal 3", goal_type="cache", target_value=90.0)
        
        goals = manager.list_goals()
        self.assertEqual(len(goals), 3)

    def test_list_goals_by_type(self):
        """Test filtering goals by type."""
        manager = GoalManager(
            goals_path=str(self.goals_path),
            state_path=str(self.state_path),
        )
        
        manager.add_goal(name="Savings 1", goal_type="savings", target_value=50.0)
        manager.add_goal(name="Savings 2", goal_type="savings", target_value=75.0)
        manager.add_goal(name="Compression 1", goal_type="compression", target_value=80.0)
        
        savings_goals = manager.list_goals(goal_type="savings")
        self.assertEqual(len(savings_goals), 2)
        
        compression_goals = manager.list_goals(goal_type="compression")
        self.assertEqual(len(compression_goals), 1)

    def test_milestone_25_percent(self):
        """Test 25% milestone alert."""
        manager = GoalManager(
            goals_path=str(self.goals_path),
            state_path=str(self.state_path),
        )
        
        goal = manager.add_goal(
            name="Milestone Test",
            goal_type="savings",
            target_value=100.0,
        )
        
        # Update to 25%
        manager.update_progress(goal.goal_id, 25.0)
        
        events = manager.check_milestones(goal.goal_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["milestone"], 25)
        
        # Check that flag is set
        progress = manager.get_progress(goal.goal_id)
        self.assertTrue(progress.milestone_25_fired)

    def test_milestone_50_percent(self):
        """Test 50% milestone alert."""
        manager = GoalManager(
            goals_path=str(self.goals_path),
            state_path=str(self.state_path),
        )
        
        goal = manager.add_goal(
            name="Milestone Test",
            goal_type="savings",
            target_value=100.0,
        )
        
        manager.update_progress(goal.goal_id, 50.0)
        
        events = manager.check_milestones(goal.goal_id)
        # When jumping to 50%, both 25% and 50% milestones fire
        self.assertEqual(len(events), 2)
        milestones = [e["milestone"] for e in events]
        self.assertIn(25, milestones)
        self.assertIn(50, milestones)

    def test_milestone_75_percent(self):
        """Test 75% milestone alert."""
        manager = GoalManager(
            goals_path=str(self.goals_path),
            state_path=str(self.state_path),
        )
        
        goal = manager.add_goal(
            name="Milestone Test",
            goal_type="savings",
            target_value=100.0,
        )
        
        manager.update_progress(goal.goal_id, 75.0)
        
        events = manager.check_milestones(goal.goal_id)
        # When jumping to 75%, 25%, 50%, and 75% milestones fire
        self.assertEqual(len(events), 3)
        milestones = [e["milestone"] for e in events]
        self.assertIn(25, milestones)
        self.assertIn(50, milestones)
        self.assertIn(75, milestones)

    def test_milestone_100_percent(self):
        """Test 100% milestone alert (goal completion)."""
        manager = GoalManager(
            goals_path=str(self.goals_path),
            state_path=str(self.state_path),
        )
        
        goal = manager.add_goal(
            name="Milestone Test",
            goal_type="savings",
            target_value=100.0,
        )
        
        manager.update_progress(goal.goal_id, 100.0)
        
        events = manager.check_milestones(goal.goal_id)
        # When jumping to 100%, all milestones fire
        self.assertEqual(len(events), 4)
        milestones = [e["milestone"] for e in events]
        self.assertIn(25, milestones)
        self.assertIn(50, milestones)
        self.assertIn(75, milestones)
        self.assertIn(100, milestones)

    def test_multiple_milestones_in_one_update(self):
        """Test firing multiple milestones in one update."""
        manager = GoalManager(
            goals_path=str(self.goals_path),
            state_path=str(self.state_path),
        )
        
        goal = manager.add_goal(
            name="Multi-Milestone Test",
            goal_type="savings",
            target_value=100.0,
        )
        
        # Jump straight to 75%
        manager.update_progress(goal.goal_id, 75.0)
        
        events = manager.check_milestones(goal.goal_id)
        # Should fire 25%, 50%, 75% all at once
        self.assertEqual(len(events), 3)
        milestones = [e["milestone"] for e in events]
        self.assertIn(25, milestones)
        self.assertIn(50, milestones)
        self.assertIn(75, milestones)

    def test_milestone_not_repeated(self):
        """Test that milestones don't fire twice."""
        manager = GoalManager(
            goals_path=str(self.goals_path),
            state_path=str(self.state_path),
        )
        
        goal = manager.add_goal(
            name="No Repeat Test",
            goal_type="savings",
            target_value=100.0,
        )
        
        # First update to 50%
        manager.update_progress(goal.goal_id, 50.0)
        events1 = manager.check_milestones(goal.goal_id)
        # Both 25% and 50% fire on first update
        self.assertEqual(len(events1), 2)
        
        # Second update to 60%
        manager.update_progress(goal.goal_id, 60.0)
        events2 = manager.check_milestones(goal.goal_id)
        self.assertEqual(len(events2), 0)  # No new milestones

    def test_pace_status_on_track(self):
        """Test pace status calculation (on track)."""
        today = datetime.now().date()
        start = (today - timedelta(days=10)).isoformat()
        end = (today + timedelta(days=20)).isoformat()
        
        manager = GoalManager(
            goals_path=str(self.goals_path),
            state_path=str(self.state_path),
        )
        
        goal = manager.add_goal(
            name="Pace Test",
            goal_type="savings",
            target_value=100.0,
            start_date=start,
            end_date=end,
        )
        
        # Update to ~33% (should be on track or close)
        progress = manager.update_progress(goal.goal_id, 33.0)
        self.assertIn(progress.pace_status, ["on_track", "ahead"])

    def test_pace_status_behind(self):
        """Test pace status calculation (behind schedule)."""
        today = datetime.now().date()
        start = (today - timedelta(days=20)).isoformat()
        end = (today + timedelta(days=10)).isoformat()
        
        manager = GoalManager(
            goals_path=str(self.goals_path),
            state_path=str(self.state_path),
        )
        
        goal = manager.add_goal(
            name="Behind Test",
            goal_type="savings",
            target_value=100.0,
            start_date=start,
            end_date=end,
        )
        
        # Only at 10% when we should be at ~66% (20 of 30 days elapsed)
        progress = manager.update_progress(goal.goal_id, 10.0)
        self.assertEqual(progress.pace_status, "behind")

    def test_pace_status_ahead(self):
        """Test pace status calculation (ahead of schedule)."""
        today = datetime.now().date()
        start = (today - timedelta(days=5)).isoformat()
        end = (today + timedelta(days=25)).isoformat()
        
        manager = GoalManager(
            goals_path=str(self.goals_path),
            state_path=str(self.state_path),
        )
        
        goal = manager.add_goal(
            name="Ahead Test",
            goal_type="savings",
            target_value=100.0,
            start_date=start,
            end_date=end,
        )
        
        # At 50% when we should only be at ~16% (5 of 30 days)
        progress = manager.update_progress(goal.goal_id, 50.0)
        self.assertEqual(progress.pace_status, "ahead")

    def test_persistence_save(self):
        """Test that goals and progress persist to disk."""
        # Create manager and add goal
        manager1 = GoalManager(
            goals_path=str(self.goals_path),
            state_path=str(self.state_path),
        )
        
        goal = manager1.add_goal(
            name="Persistent Goal",
            goal_type="savings",
            target_value=100.0,
        )
        
        manager1.update_progress(goal.goal_id, 50.0)
        
        # Create new manager instance and reload
        manager2 = GoalManager(
            goals_path=str(self.goals_path),
            state_path=str(self.state_path),
        )
        
        loaded_goal = manager2.get_goal(goal.goal_id)
        self.assertIsNotNone(loaded_goal)
        self.assertEqual(loaded_goal.name, "Persistent Goal")
        
        loaded_progress = manager2.get_progress(goal.goal_id)
        self.assertIsNotNone(loaded_progress)
        self.assertEqual(loaded_progress.current_value, 50.0)

    def test_summary_stats(self):
        """Test summary statistics calculation."""
        manager = GoalManager(
            goals_path=str(self.goals_path),
            state_path=str(self.state_path),
        )
        
        goal1 = manager.add_goal(name="Goal 1", goal_type="savings", target_value=100.0)
        goal2 = manager.add_goal(name="Goal 2", goal_type="savings", target_value=100.0)
        
        manager.update_progress(goal1.goal_id, 100.0)  # 100%
        manager.update_progress(goal2.goal_id, 50.0)   # 50%
        
        stats = manager.get_summary_stats()
        
        self.assertEqual(stats["total_goals"], 2)
        self.assertEqual(stats["completed_goals"], 1)
        self.assertEqual(stats["active_goals"], 1)
        # Average of 100% and 50% = 75%
        self.assertAlmostEqual(stats["avg_progress"], 75.0, places=1)

    def test_custom_metric_goal(self):
        """Test custom metric goal creation."""
        manager = GoalManager(
            goals_path=str(self.goals_path),
            state_path=str(self.state_path),
        )
        
        goal = manager.add_goal(
            name="Cache Performance",
            goal_type="metric",
            target_value=95.0,
            metric_name="cache_hit_rate",
        )
        
        self.assertEqual(goal.goal_type, "metric")
        self.assertEqual(goal.metric_name, "cache_hit_rate")

    def test_rolling_window_goal(self):
        """Test rolling window goal creation."""
        manager = GoalManager(
            goals_path=str(self.goals_path),
            state_path=str(self.state_path),
        )
        
        goal = manager.add_goal(
            name="Weekly Pace",
            goal_type="savings",
            target_value=25.0,
            rolling_window=True,
        )
        
        self.assertTrue(goal.rolling_window)


if __name__ == "__main__":
    unittest.main()
