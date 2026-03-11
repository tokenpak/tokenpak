"""tokenpak.agent.agentic.workflow_performance — Performance tracking and ranking for workflows.

Tracks execution metrics per workflow template (success rate, duration, tokens, regressions)
and provides deterministic ranking for selecting the best workflow for a problem class.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from workflow import WorkflowManager, WorkflowStatus, WorkflowRecord

DEFAULT_STATS_FILE = Path(os.path.expanduser("~/.tokenpak/workflow_stats.json"))


@dataclass
class WorkflowStats:
    """Performance statistics for a workflow template."""
    template_name: str
    success_count: int = 0
    failure_count: int = 0
    total_duration_seconds: float = 0.0
    total_tokens: int = 0
    regression_count: int = 0
    last_updated: float = field(default_factory=time.time)
    executions: List[Dict] = field(default_factory=list)  # Detailed per-execution records

    def success_rate(self) -> float:
        """Return success rate (0.0-1.0)."""
        total = self.success_count + self.failure_count
        if total == 0:
            return 0.5  # Default for no data
        return self.success_count / total

    def avg_duration_seconds(self) -> float:
        """Return average duration in seconds."""
        total = self.success_count + self.failure_count
        if total == 0:
            return float('inf')
        return self.total_duration_seconds / total

    def avg_tokens(self) -> float:
        """Return average tokens per execution."""
        total = self.success_count + self.failure_count
        if total == 0:
            return 0.0
        return self.total_tokens / total

    def regression_rate(self) -> float:
        """Return regression rate (0.0-1.0)."""
        total = self.success_count + self.failure_count
        if total == 0:
            return 0.0
        return self.regression_count / total

    def to_dict(self) -> Dict:
        d = asdict(self)
        d['last_updated'] = self.last_updated
        return d

    @classmethod
    def from_dict(cls, d: Dict) -> WorkflowStats:
        d = dict(d)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class WorkflowPerformanceTracker:
    """Tracks execution metrics and ranks workflows by performance."""

    def __init__(self, stats_file: Optional[Path] = None):
        self._stats_file = stats_file or DEFAULT_STATS_FILE
        self._stats: Dict[str, WorkflowStats] = {}
        self._load_stats()

    def _load_stats(self):
        """Load stats from disk."""
        if self._stats_file.exists():
            try:
                data = json.loads(self._stats_file.read_text())
                self._stats = {
                    name: WorkflowStats.from_dict(stat_data)
                    for name, stat_data in data.items()
                }
            except Exception:
                self._stats = {}
        else:
            self._stats_file.parent.mkdir(parents=True, exist_ok=True)

    def _save_stats(self):
        """Save stats to disk."""
        self._stats_file.parent.mkdir(parents=True, exist_ok=True)
        data = {name: stats.to_dict() for name, stats in self._stats.items()}
        tmp = self._stats_file.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self._stats_file)

    def record_execution(
        self,
        template_name: str,
        success: bool,
        duration_seconds: float,
        tokens_used: int = 0,
        regression: bool = False,
        metadata: Optional[Dict] = None
    ) -> WorkflowStats:
        """Record a workflow execution."""
        if template_name not in self._stats:
            self._stats[template_name] = WorkflowStats(template_name=template_name)

        stats = self._stats[template_name]
        if success:
            stats.success_count += 1
            stats.total_duration_seconds += duration_seconds
            stats.total_tokens += tokens_used
        else:
            stats.failure_count += 1

        if regression:
            stats.regression_count += 1

        # Record detailed execution
        stats.executions.append({
            'timestamp': time.time(),
            'success': success,
            'duration': duration_seconds,
            'tokens': tokens_used,
            'regression': regression,
            'metadata': metadata or {}
        })

        # Keep last 1000 executions to avoid unbounded growth
        if len(stats.executions) > 1000:
            stats.executions = stats.executions[-1000:]

        stats.last_updated = time.time()
        self._save_stats()
        return stats

    def get_stats(self, template_name: str) -> Optional[WorkflowStats]:
        """Get stats for a template."""
        return self._stats.get(template_name)

    def all_stats(self) -> Dict[str, WorkflowStats]:
        """Get all stats."""
        return dict(self._stats)

    def score_template(
        self,
        template_name: str,
        max_duration_seconds: float = 300.0,
        max_tokens: int = 100000
    ) -> float:
        """
        Score a template using the ranking formula:
        Score = (success_rate × 0.5) + (speed_score × 0.2) + (token_efficiency × 0.2) + (no_regression × 0.1)

        Args:
            template_name: The template to score
            max_duration_seconds: Reference duration for speed_score (lower is better)
            max_tokens: Reference token count for token_efficiency (lower is better)

        Returns:
            Score from 0.0 to 1.0
        """
        stats = self.get_stats(template_name)
        if stats is None or (stats.success_count + stats.failure_count) == 0:
            return 0.0  # No data = low score

        # Success rate: 0.0-1.0
        success_score = stats.success_rate()

        # Speed score: inverse of normalized duration
        # Lower duration = higher score
        avg_duration = stats.avg_duration_seconds()
        if avg_duration == float('inf'):
            speed_score = 0.0
        else:
            speed_score = max(0.0, 1.0 - (avg_duration / max_duration_seconds))

        # Token efficiency: inverse of normalized tokens
        # Lower tokens = higher score
        avg_tokens_val = stats.avg_tokens()
        token_efficiency = max(0.0, 1.0 - (avg_tokens_val / max_tokens))

        # No regression: penalize by regression rate
        no_regression_score = 1.0 - stats.regression_rate()

        score = (
            success_score * 0.5 +
            speed_score * 0.2 +
            token_efficiency * 0.2 +
            no_regression_score * 0.1
        )
        return round(score, 4)

    def rank_templates(
        self,
        task_type: str,
        candidates: Optional[List[str]] = None,
        max_duration_seconds: float = 300.0,
        max_tokens: int = 100000
    ) -> List[Tuple[str, float, WorkflowStats]]:
        """
        Rank templates for a given task type.

        Args:
            task_type: The task/problem class (for logging/filtering)
            candidates: Optional list of templates to rank. If None, rank all available.
            max_duration_seconds: Reference duration for speed scoring
            max_tokens: Reference token count for efficiency scoring

        Returns:
            List of (template_name, score, stats) sorted by score descending
        """
        if candidates is None:
            candidates = list(self._stats.keys())

        ranked = []
        for template_name in candidates:
            score = self.score_template(template_name, max_duration_seconds, max_tokens)
            stats = self.get_stats(template_name)
            ranked.append((template_name, score, stats or WorkflowStats(template_name)))

        # Sort by score descending (higher is better)
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked

    def clear_stats(self, template_name: Optional[str] = None):
        """Clear stats for a template or all templates."""
        if template_name:
            self._stats.pop(template_name, None)
        else:
            self._stats = {}
        self._save_stats()


def record_workflow_execution(
    workflow: WorkflowRecord,
    tokens_used: int = 0,
    regression: bool = False,
    tracker: Optional[WorkflowPerformanceTracker] = None
):
    """
    Convenience function to record a completed workflow execution.
    
    Args:
        workflow: The completed WorkflowRecord
        tokens_used: Total tokens used during execution
        regression: Whether a regression was detected
        tracker: Optional tracker instance. If None, creates a new one.
    """
    if not tracker:
        tracker = WorkflowPerformanceTracker()

    if workflow.template is None:
        return  # Skip workflows without a template

    success = workflow.status == WorkflowStatus.COMPLETED
    duration = workflow.duration_seconds() or 0.0

    tracker.record_execution(
        template_name=workflow.template,
        success=success,
        duration_seconds=duration,
        tokens_used=tokens_used,
        regression=regression,
        metadata={'workflow_id': workflow.id, 'workflow_name': workflow.name}
    )


# Global tracker instance
_tracker = None


def get_tracker(stats_file: Optional[Path] = None) -> WorkflowPerformanceTracker:
    """Get or create the global tracker instance."""
    global _tracker
    if _tracker is None:
        _tracker = WorkflowPerformanceTracker(stats_file)
    return _tracker
