# SPDX-License-Identifier: Apache-2.0
"""TokenPak Savings Goals — Goal Management, Tracking, and Calculation

Goal tracking system for TokenPak with support for:
- Savings dollar amount goals
- Compression ratio goals (%)
- Cache hit rate goals
- Custom metrics goals
- Rolling window goals (weekly pace)
- Milestone alerts (25%, 50%, 75%, 100%)
- Pace alerts (on track, ahead, behind)
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

try:
    import yaml as _yaml

    def _load_yaml(path: str) -> dict:
        with open(path, "r") as f:
            return _yaml.safe_load(f) or {}

    def _save_yaml(path: str, data: dict):
        with open(path, "w") as f:
            _yaml.safe_dump(data, f, default_flow_style=False)

except ImportError:

    def _load_yaml(path: str) -> dict:
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_yaml(path: str, data: dict):
        with open(path, "w") as f:
            json.dump(data, f, indent=2)


class GoalType(Enum):
    """Goal type classification."""

    SAVINGS = "savings"  # Dollar savings goal
    COMPRESSION = "compression"  # Compression ratio goal (%)
    CACHE = "cache"  # Cache hit rate goal (%)
    METRIC = "metric"  # Custom metric goal (user-defined)


class GoalStatus(Enum):
    """Goal status classification."""

    ACTIVE = "active"
    COMPLETED = "completed"
    PAUSED = "paused"
    BEHIND = "behind"  # Behind schedule
    ON_TRACK = "on_track"  # On track
    AHEAD = "ahead"  # Ahead of schedule


@dataclass
class GoalProgress:
    """Tracks progress for a single goal."""

    goal_id: str
    current_value: float = 0.0
    target_value: float = 100.0
    progress_percent: float = 0.0
    milestone_25_fired: bool = False
    milestone_50_fired: bool = False
    milestone_75_fired: bool = False
    milestone_100_fired: bool = False
    pace_status: str = "on_track"  # on_track, ahead, behind
    pace_alert_fired: bool = False
    last_update: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> GoalProgress:
        return cls(**data)


@dataclass
class Goal:
    """Single savings/metric goal definition."""

    goal_id: str
    name: str
    goal_type: str  # savings, compression, cache, metric
    target_value: float  # Dollar amount or percentage
    start_date: str  # ISO format: YYYY-MM-DD
    end_date: str  # ISO format: YYYY-MM-DD
    description: str = ""
    metric_name: str = ""  # For custom metrics (e.g., "inference_cost")
    rolling_window: bool = False  # For weekly pace goals
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Goal:
        return cls(**data)

    def days_remaining(self) -> int:
        """Calculate days remaining until goal end date."""
        end = datetime.fromisoformat(self.end_date).date()
        today = datetime.now().date()
        delta = (end - today).days
        return max(0, delta)

    def days_elapsed(self) -> int:
        """Calculate days elapsed since goal start date."""
        start = datetime.fromisoformat(self.start_date).date()
        today = datetime.now().date()
        delta = (today - start).days
        return max(0, delta)

    def total_days(self) -> int:
        """Calculate total days for this goal."""
        start = datetime.fromisoformat(self.start_date).date()
        end = datetime.fromisoformat(self.end_date).date()
        delta = (end - start).days
        return max(1, delta)

    def expected_progress_percent(self) -> float:
        """Calculate expected progress based on elapsed time.

        Returns:
            Percent (0-100) of time elapsed
        """
        if self.total_days() == 0:
            return 100.0
        elapsed = self.days_elapsed()
        return min(100.0, (elapsed / self.total_days()) * 100)


class GoalManager:
    """Manages goal creation, loading, tracking, and persistence."""

    def __init__(self, goals_path: Optional[str] = None, state_path: Optional[str] = None):
        """Initialize goal manager.

        Args:
            goals_path: Path to goals.yaml config file
            state_path: Path to goal_state.json tracking file
        """
        self.goals_path = Path(goals_path or (Path.home() / ".tokenpak" / "goals.yaml"))
        self.state_path = Path(state_path or (Path.home() / ".tokenpak" / "goal_state.json"))
        self.goals: Dict[str, Goal] = {}
        self.progress: Dict[str, GoalProgress] = {}
        self._load()

    def _load(self):
        """Load goals and state from disk."""
        # Load goals from YAML
        if self.goals_path.exists():
            config = _load_yaml(str(self.goals_path))
            goals_data = config.get("goals", [])
            for g in goals_data:
                goal = Goal.from_dict(g)
                self.goals[goal.goal_id] = goal

        # Load state from JSON
        if self.state_path.exists():
            try:
                with open(self.state_path, "r") as f:
                    state_data = json.load(f)
                for goal_id, prog_data in state_data.items():
                    self.progress[goal_id] = GoalProgress.from_dict(prog_data)
            except Exception:
                pass

        # Initialize progress for any goals without state
        for goal_id, goal in self.goals.items():
            if goal_id not in self.progress:
                self.progress[goal_id] = GoalProgress(
                    goal_id=goal_id, target_value=goal.target_value
                )

    def _save(self):
        """Persist goals and state to disk."""
        # Ensure parent directories exist
        self.goals_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

        # Save goals to YAML
        goals_list = [goal.to_dict() for goal in self.goals.values()]
        config = {"goals": goals_list}
        _save_yaml(str(self.goals_path), config)

        # Save state to JSON
        state = {goal_id: prog.to_dict() for goal_id, prog in self.progress.items()}
        with open(self.state_path, "w") as f:
            json.dump(state, f, indent=2)

    def add_goal(
        self,
        name: str,
        goal_type: str,
        target_value: float,
        start_date: str = None,
        end_date: str = None,
        description: str = "",
        metric_name: str = "",
        rolling_window: bool = False,
    ) -> Goal:
        """Create a new goal.

        Args:
            name: Human-readable goal name
            goal_type: "savings", "compression", "cache", or "metric"
            target_value: Target value (dollar amount or percentage)
            start_date: ISO format date (default: today)
            end_date: ISO format date (default: 30 days from start)
            description: Optional description
            metric_name: For custom metrics
            rolling_window: For weekly pace goals

        Returns:
            Created Goal
        """
        if start_date is None:
            start_date = datetime.now().date().isoformat()
        if end_date is None:
            end_date = (datetime.now().date() + timedelta(days=30)).isoformat()

        # Generate unique ID
        goal_id = f"goal_{int(time.time() * 1000) % 1000000}"

        goal = Goal(
            goal_id=goal_id,
            name=name,
            goal_type=goal_type,
            target_value=target_value,
            start_date=start_date,
            end_date=end_date,
            description=description,
            metric_name=metric_name,
            rolling_window=rolling_window,
        )

        self.goals[goal_id] = goal
        self.progress[goal_id] = GoalProgress(goal_id=goal_id, target_value=target_value)
        self._save()

        return goal

    def edit_goal(self, goal_id: str, **kwargs) -> Optional[Goal]:
        """Edit an existing goal.

        Args:
            goal_id: Goal to edit
            **kwargs: Fields to update

        Returns:
            Updated Goal or None if not found
        """
        if goal_id not in self.goals:
            return None

        goal = self.goals[goal_id]
        for key, value in kwargs.items():
            if hasattr(goal, key):
                setattr(goal, key, value)

        # Update progress target if needed
        if "target_value" in kwargs:
            self.progress[goal_id].target_value = kwargs["target_value"]

        self._save()
        return goal

    def delete_goal(self, goal_id: str) -> bool:
        """Delete a goal.

        Args:
            goal_id: Goal to delete

        Returns:
            True if deleted, False if not found
        """
        if goal_id not in self.goals:
            return False

        del self.goals[goal_id]
        if goal_id in self.progress:
            del self.progress[goal_id]

        self._save()
        return True

    def update_progress(
        self,
        goal_id: str,
        current_value: float,
        source: str = "manual",
    ) -> Optional[GoalProgress]:
        """Update progress for a goal.

        Args:
            goal_id: Goal to update
            current_value: New current value
            source: Source of update (manual, proxy, calculation)

        Returns:
            Updated GoalProgress or None if goal not found
        """
        if goal_id not in self.goals or goal_id not in self.progress:
            return None

        goal = self.goals[goal_id]
        progress = self.progress[goal_id]

        # Update current value and calculate progress percent
        progress.current_value = current_value
        progress.progress_percent = min(100.0, (current_value / goal.target_value) * 100)
        progress.last_update = time.time()

        # Calculate pace status
        expected = goal.expected_progress_percent()
        actual = progress.progress_percent
        tolerance = 5.0  # 5% tolerance for "on track"

        if actual >= expected + tolerance:
            progress.pace_status = "ahead"
        elif actual <= expected - tolerance:
            progress.pace_status = "behind"
        else:
            progress.pace_status = "on_track"

        self._save()
        return progress

    def get_goal(self, goal_id: str) -> Optional[Goal]:
        """Retrieve a goal by ID."""
        return self.goals.get(goal_id)

    def get_progress(self, goal_id: str) -> Optional[GoalProgress]:
        """Retrieve progress for a goal."""
        return self.progress.get(goal_id)

    def list_goals(
        self, status: Optional[str] = None, goal_type: Optional[str] = None
    ) -> List[Goal]:
        """List all goals, optionally filtered.

        Args:
            status: Filter by status (active, completed, paused)
            goal_type: Filter by type (savings, compression, cache, metric)

        Returns:
            List of Goal objects
        """
        goals = list(self.goals.values())

        if status:
            # Filter by status based on progress
            filtered = []
            for g in goals:
                prog = self.progress.get(g.goal_id)
                if prog and prog.progress_percent >= 100:
                    if status == "completed":
                        filtered.append(g)
                elif status == "active":
                    filtered.append(g)
            goals = filtered

        if goal_type:
            goals = [g for g in goals if g.goal_type == goal_type]

        return goals

    def check_milestones(self, goal_id: str) -> List[dict]:
        """Check and fire milestone alerts for a goal.

        Returns:
            List of fired milestone events
        """
        if goal_id not in self.progress:
            return []

        progress = self.progress[goal_id]
        goal = self.goals[goal_id]
        percent = progress.progress_percent

        events = []

        # Check milestones
        if percent >= 25 and not progress.milestone_25_fired:
            progress.milestone_25_fired = True
            events.append(
                {
                    "type": "milestone",
                    "goal_id": goal_id,
                    "goal_name": goal.name,
                    "milestone": 25,
                    "message": f"🎉 {goal.name}: 25% complete!",
                }
            )

        if percent >= 50 and not progress.milestone_50_fired:
            progress.milestone_50_fired = True
            events.append(
                {
                    "type": "milestone",
                    "goal_id": goal_id,
                    "goal_name": goal.name,
                    "milestone": 50,
                    "message": f"🎯 {goal.name}: 50% complete!",
                }
            )

        if percent >= 75 and not progress.milestone_75_fired:
            progress.milestone_75_fired = True
            events.append(
                {
                    "type": "milestone",
                    "goal_id": goal_id,
                    "goal_name": goal.name,
                    "milestone": 75,
                    "message": f"💪 {goal.name}: 75% complete!",
                }
            )

        if percent >= 100 and not progress.milestone_100_fired:
            progress.milestone_100_fired = True
            events.append(
                {
                    "type": "milestone",
                    "goal_id": goal_id,
                    "goal_name": goal.name,
                    "milestone": 100,
                    "message": f"✅ {goal.name}: GOAL ACHIEVED!",
                }
            )

        if events:
            self._save()

        return events

    def check_pace_alerts(self, goal_id: str) -> Optional[dict]:
        """Check and fire pace alert for a goal.

        Returns:
            Alert event or None if not needed
        """
        if goal_id not in self.progress:
            return None

        progress = self.progress[goal_id]
        goal = self.goals[goal_id]

        if progress.pace_alert_fired:
            return None

        # Only alert for "behind" status
        if progress.pace_status == "behind":
            progress.pace_alert_fired = True
            self._save()
            return {
                "type": "pace",
                "goal_id": goal_id,
                "goal_name": goal.name,
                "status": "behind",
                "message": f"⚠️ {goal.name}: Behind schedule! ({progress.progress_percent:.0f}% vs expected {goal.expected_progress_percent():.0f}%)",
            }

        return None

    def get_summary_stats(self) -> dict:
        """Get summary statistics for all goals.

        Returns:
            Dict with summary stats
        """
        goals_list = self.list_goals()

        if not goals_list:
            return {
                "total_goals": 0,
                "active_goals": 0,
                "completed_goals": 0,
                "avg_progress": 0.0,
            }

        progresses = [self.progress.get(g.goal_id) for g in goals_list]
        completed = sum(1 for p in progresses if p and p.progress_percent >= 100)

        return {
            "total_goals": len(goals_list),
            "active_goals": len(goals_list) - completed,
            "completed_goals": completed,
            "avg_progress": sum(p.progress_percent for p in progresses if p) / len(progresses)
            if progresses
            else 0.0,
        }
