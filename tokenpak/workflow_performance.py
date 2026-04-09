"""tokenpak.workflow_performance — Performance tracking and ranking for workflow templates.

Tracks per-template execution stats (success/failure/duration/tokens/regressions)
and provides a scoring/ranking function so the best template is tried first.

Stats are persisted to ~/.tokenpak/workflow_stats.json.

Usage::

    from tokenpak.workflow_performance import get_tracker, record_workflow_execution

    tracker = get_tracker()

    # After a workflow completes:
    record_workflow_execution(workflow_record, tokens_used=1500, regression=False)

    # Before starting — pick best template:
    ranked = tracker.rank_templates("deploy", candidates=["deploy", "proxy", "release"])
    best_template = ranked[0][0]

Scoring formula::

    score = (success_rate × 0.5) + (speed_score × 0.2)
          + (token_efficiency × 0.2) + (no_regression × 0.1)

All sub-scores are normalized to [0.0, 1.0].  Templates with no data score 0.0.
"""

from __future__ import annotations

import json
import time
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

STATS_PATH = Path.home() / ".tokenpak" / "workflow_stats.json"
MAX_HISTORY = 1_000  # cap per-template execution history


# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class WorkflowStats:
    """Aggregate counters for a single workflow template."""

    template: str
    success_count: int = 0
    failure_count: int = 0
    total_duration_seconds: float = 0.0
    total_tokens: int = 0
    regression_count: int = 0
    # Per-execution history (capped at MAX_HISTORY)
    history: List[Dict] = field(default_factory=list)

    # ── Derived metrics ──────────────────────────────────────────────────────

    @property
    def total_runs(self) -> int:
        return self.success_count + self.failure_count

    @property
    def success_rate(self) -> float:
        """Fraction of runs that succeeded (0.0–1.0).  0.0 when no data."""
        if self.total_runs == 0:
            return 0.0
        return self.success_count / self.total_runs

    @property
    def avg_duration(self) -> float:
        """Mean execution time in seconds across all runs."""
        if self.total_runs == 0:
            return 0.0
        return self.total_duration_seconds / self.total_runs

    @property
    def avg_tokens(self) -> float:
        """Mean token count across all runs."""
        if self.total_runs == 0:
            return 0.0
        return self.total_tokens / self.total_runs

    @property
    def regression_rate(self) -> float:
        """Fraction of successful runs that had a regression (0.0–1.0)."""
        if self.success_count == 0:
            return 0.0
        return self.regression_count / self.success_count

    # ── Serialisation ────────────────────────────────────────────────────────

    def to_dict(self) -> Dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: Dict) -> "WorkflowStats":
        return cls(
            template=d["template"],
            success_count=d.get("success_count", 0),
            failure_count=d.get("failure_count", 0),
            total_duration_seconds=d.get("total_duration_seconds", 0.0),
            total_tokens=d.get("total_tokens", 0),
            regression_count=d.get("regression_count", 0),
            history=d.get("history", []),
        )


# ── Tracker ──────────────────────────────────────────────────────────────────


class WorkflowPerformanceTracker:
    """Persist and query per-template workflow performance statistics."""

    def __init__(self, stats_path: Path = STATS_PATH) -> None:
        self._path = stats_path
        self._stats: Dict[str, WorkflowStats] = {}
        self._load()

    # ── Persistence ─────────────────────────────────────────────────────────

    def _load(self) -> None:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text())
                self._stats = {k: WorkflowStats.from_dict(v) for k, v in raw.items()}
            except (json.JSONDecodeError, KeyError):
                self._stats = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({k: v.to_dict() for k, v in self._stats.items()}, indent=2))
        tmp.replace(self._path)

    # ── Recording ────────────────────────────────────────────────────────────

    def record(
        self,
        template: str,
        success: bool,
        duration_seconds: float,
        tokens_used: int = 0,
        regression: bool = False,
    ) -> WorkflowStats:
        """Record the outcome of a single workflow execution.

        Args:
            template: Template name (e.g. ``"deploy"``).
            success: Whether the workflow completed successfully.
            duration_seconds: Wall-clock execution time.
            tokens_used: Total tokens consumed.
            regression: Whether a regression was detected post-run.

        Returns:
            Updated :class:`WorkflowStats` for the template.
        """
        if template not in self._stats:
            self._stats[template] = WorkflowStats(template=template)

        stats = self._stats[template]

        if success:
            stats.success_count += 1
            if regression:
                stats.regression_count += 1
        else:
            stats.failure_count += 1

        stats.total_duration_seconds += duration_seconds
        stats.total_tokens += tokens_used

        entry = {
            "ts": time.time(),
            "success": success,
            "duration": duration_seconds,
            "tokens": tokens_used,
            "regression": regression,
        }
        stats.history.append(entry)
        if len(stats.history) > MAX_HISTORY:
            stats.history = stats.history[-MAX_HISTORY:]

        self._save()
        return stats

    # ── Scoring & ranking ────────────────────────────────────────────────────

    def score_template(
        self,
        template: str,
        *,
        max_duration: float = 300.0,
        max_tokens: int = 50_000,
    ) -> float:
        """Compute a scalar score in [0.0, 1.0] for *template*.

        Formula::

            score = (success_rate × 0.5)
                  + (speed_score   × 0.2)   # lower duration → higher score
                  + (token_eff     × 0.2)   # lower tokens   → higher score
                  + (no_regression × 0.1)   # lower regression rate → higher score

        Returns 0.0 for unknown templates (no data yet).
        """
        stats = self._stats.get(template)
        if stats is None or stats.total_runs == 0:
            return 0.0

        success_score = stats.success_rate

        # Speed: 1.0 when avg_duration == 0, 0.0 when avg_duration >= max_duration
        speed_score = max(0.0, 1.0 - stats.avg_duration / max_duration)

        # Token efficiency: 1.0 when avg_tokens == 0, 0.0 when >= max_tokens
        token_eff = max(0.0, 1.0 - stats.avg_tokens / max_tokens)

        # Regression-free: 1.0 when no regressions
        no_regression = 1.0 - stats.regression_rate

        return success_score * 0.5 + speed_score * 0.2 + token_eff * 0.2 + no_regression * 0.1

    def rank_templates(
        self,
        task_type: str,
        *,
        candidates: Optional[Sequence[str]] = None,
        max_duration: float = 300.0,
        max_tokens: int = 50_000,
    ) -> List[Tuple[str, float]]:
        """Return templates sorted by score, highest first.

        Args:
            task_type: Informational label for the class of task (unused for
                scoring; present for future per-class filtering).
            candidates: Explicit list of template names to rank.  When omitted,
                all known templates are ranked.
            max_duration: Upper bound for speed normalisation (seconds).
            max_tokens: Upper bound for token-efficiency normalisation.

        Returns:
            List of ``(template_name, score)`` tuples, descending by score.
        """
        names: Sequence[str]
        if candidates is not None:
            names = list(candidates)
        else:
            names = list(self._stats.keys())

        scored = [
            (name, self.score_template(name, max_duration=max_duration, max_tokens=max_tokens))
            for name in names
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    # ── Accessors ────────────────────────────────────────────────────────────

    def get_stats(self, template: str) -> Optional[WorkflowStats]:
        """Return :class:`WorkflowStats` for *template*, or ``None``."""
        return self._stats.get(template)

    def all_stats(self) -> Dict[str, WorkflowStats]:
        """Return a copy of all tracked stats."""
        return {k: deepcopy(v) for k, v in self._stats.items()}


# ── Singleton + convenience ──────────────────────────────────────────────────

_tracker: Optional[WorkflowPerformanceTracker] = None


def get_tracker(stats_path: Path = STATS_PATH) -> WorkflowPerformanceTracker:
    """Return the module-level singleton :class:`WorkflowPerformanceTracker`."""
    global _tracker
    if _tracker is None:
        _tracker = WorkflowPerformanceTracker(stats_path=stats_path)
    return _tracker


def record_workflow_execution(
    workflow,  # WorkflowRecord from tokenpak.agent.agentic.workflow
    *,
    tokens_used: int = 0,
    regression: bool = False,
    tracker: Optional[WorkflowPerformanceTracker] = None,
) -> Optional[WorkflowStats]:
    """Convenience wrapper — record a completed :class:`WorkflowRecord`.

    If the workflow has no template set, returns ``None`` and does nothing.

    Args:
        workflow: A :class:`~tokenpak.agent.agentic.workflow.WorkflowRecord`.
        tokens_used: Total tokens consumed by this run.
        regression: Whether a regression was detected post-completion.
        tracker: Optional explicit tracker; defaults to module singleton.
    """
    if workflow.template is None:
        return None

    from tokenpak.agent.agentic.workflow import WorkflowStatus  # local import

    success = workflow.status == WorkflowStatus.COMPLETED
    duration = workflow.duration_seconds() or 0.0

    t = tracker or get_tracker()
    return t.record(
        template=workflow.template,
        success=success,
        duration_seconds=duration,
        tokens_used=tokens_used,
        regression=regression,
    )
