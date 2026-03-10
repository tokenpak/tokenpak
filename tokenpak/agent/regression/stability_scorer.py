# SPDX-License-Identifier: MIT
"""
Stability Scorer for TokenPak workflows.

Rates workflow reliability based on pass rate, output variance, retry rate,
token volatility, and validation success rate. Stable workflows receive tighter
budgets; unstable workflows receive expanded budgets with more support.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class RunRecord:
    """Single execution record for a workflow."""

    timestamp: str
    passed: bool
    retried: bool
    token_count: int
    output_text: str
    validation_passed: bool

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RunRecord":
        return cls(**data)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StabilityScore:
    """Computed stability score for a workflow."""

    workflow_id: str
    score: float  # 0.0 (chaotic) to 1.0 (rock solid)
    pass_rate: float
    retry_rate: float
    token_volatility_norm: float
    output_variance: float
    validation_success_rate: float
    run_count: int
    budget_multiplier: float
    budget_tier: str  # "tight" | "normal" | "expanded"
    computed_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StabilityScore":
        return cls(**data)


# ---------------------------------------------------------------------------
# Core scorer
# ---------------------------------------------------------------------------

_BUDGET_TIGHT = 0.70
_BUDGET_NORMAL = 1.00
_BUDGET_EXPANDED = 1.30

_SCORE_HIGH = 0.8  # above → tight budget
_SCORE_LOW = 0.5   # below → expanded budget


def _edit_distance_ratio(a: str, b: str) -> float:
    """
    Return normalised edit distance (0=identical, 1=completely different).

    Uses a simple character-level Levenshtein ratio. For large outputs the
    comparison is capped at the first 2000 chars to stay fast.
    """
    a, b = a[:2000], b[:2000]
    if not a and not b:
        return 0.0
    if not a or not b:
        return 1.0
    if a == b:
        return 0.0

    len_a, len_b = len(a), len(b)
    # DP Levenshtein – small strings only
    prev = list(range(len_b + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len_b
        for j, cb in enumerate(b, 1):
            if ca == cb:
                curr[j] = prev[j - 1]
            else:
                curr[j] = 1 + min(prev[j], curr[j - 1], prev[j - 1])
        prev = curr

    dist = prev[len_b]
    return dist / max(len_a, len_b)


def _compute_output_variance(records: List[RunRecord]) -> float:
    """
    Average pairwise edit-distance between consecutive outputs.
    Returns 0.0 for fewer than 2 records.
    """
    if len(records) < 2:
        return 0.0
    pairs = [
        _edit_distance_ratio(records[i].output_text, records[i + 1].output_text)
        for i in range(len(records) - 1)
    ]
    return statistics.mean(pairs)


def _normalise_token_volatility(records: List[RunRecord]) -> float:
    """
    Normalised token stddev.  Divides raw stddev by mean so it's scale-free.
    Clamped to [0, 1].
    """
    counts = [r.token_count for r in records]
    if len(counts) < 2:
        return 0.0
    mean = statistics.mean(counts)
    if mean == 0:
        return 0.0
    stddev = statistics.stdev(counts)
    return min(stddev / mean, 1.0)


def compute_stability(
    workflow_id: str,
    records: List[RunRecord],
) -> StabilityScore:
    """
    Compute a StabilityScore from a list of RunRecords.

    Formula:
        stability = (pass_rate × 0.30)
                  + ((1 - retry_rate) × 0.25)
                  + ((1 - token_volatility_norm) × 0.20)
                  + (validation_success_rate × 0.25)

    Args:
        workflow_id: Identifier for the workflow.
        records: History of execution records.

    Returns:
        StabilityScore with budget recommendation.
    """
    if not records:
        # No history → assume unstable
        return StabilityScore(
            workflow_id=workflow_id,
            score=0.0,
            pass_rate=0.0,
            retry_rate=1.0,
            token_volatility_norm=1.0,
            output_variance=1.0,
            validation_success_rate=0.0,
            run_count=0,
            budget_multiplier=_BUDGET_EXPANDED,
            budget_tier="expanded",
            computed_at=_now(),
        )

    n = len(records)
    pass_rate = sum(1 for r in records if r.passed) / n
    retry_rate = sum(1 for r in records if r.retried) / n
    validation_success_rate = sum(1 for r in records if r.validation_passed) / n
    token_volatility_norm = _normalise_token_volatility(records)
    output_variance = _compute_output_variance(records)

    score = (
        pass_rate * 0.30
        + (1 - retry_rate) * 0.25
        + (1 - token_volatility_norm) * 0.20
        + validation_success_rate * 0.25
    )
    score = max(0.0, min(1.0, score))

    if score > _SCORE_HIGH:
        budget_multiplier = _BUDGET_TIGHT
        budget_tier = "tight"
    elif score < _SCORE_LOW:
        budget_multiplier = _BUDGET_EXPANDED
        budget_tier = "expanded"
    else:
        budget_multiplier = _BUDGET_NORMAL
        budget_tier = "normal"

    return StabilityScore(
        workflow_id=workflow_id,
        score=round(score, 4),
        pass_rate=round(pass_rate, 4),
        retry_rate=round(retry_rate, 4),
        token_volatility_norm=round(token_volatility_norm, 4),
        output_variance=round(output_variance, 4),
        validation_success_rate=round(validation_success_rate, 4),
        run_count=n,
        budget_multiplier=budget_multiplier,
        budget_tier=budget_tier,
        computed_at=_now(),
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StabilityScorer:
    """
    Persistent stability scorer for TokenPak workflows.

    Stores run records and computed scores in
    ``~/.tokenpak/stability_scores.json``.
    """

    def __init__(self, store_path: Optional[str] = None) -> None:
        if store_path is None:
            store_path = str(Path.home() / ".tokenpak" / "stability_scores.json")
        self.store_path = Path(store_path)
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._data: Dict[str, Any] = self._load()

    # ---- persistence -------------------------------------------------------

    def _load(self) -> Dict[str, Any]:
        if self.store_path.exists():
            try:
                with self.store_path.open() as fh:
                    return json.load(fh)
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save(self) -> None:
        with self.store_path.open("w") as fh:
            json.dump(self._data, fh, indent=2)

    # ---- public API --------------------------------------------------------

    def record_run(self, workflow_id: str, record: RunRecord) -> None:
        """Append a run record for a workflow and persist to disk."""
        entry = self._data.setdefault(
            workflow_id,
            {"records": [], "score": None},
        )
        entry["records"].append(record.to_dict())
        self._save()

    def get_records(self, workflow_id: str) -> List[RunRecord]:
        """Return all stored RunRecords for a workflow."""
        raw = self._data.get(workflow_id, {}).get("records", [])
        return [RunRecord.from_dict(r) for r in raw]

    def score_workflow(self, workflow_id: str) -> StabilityScore:
        """
        Compute (or recompute) stability score from stored records.

        The score is cached inside the store for quick retrieval but always
        recomputed fresh on each call to this method.
        """
        records = self.get_records(workflow_id)
        score = compute_stability(workflow_id, records)
        entry = self._data.setdefault(workflow_id, {"records": [], "score": None})
        entry["score"] = score.to_dict()
        self._save()
        return score

    def get_cached_score(self, workflow_id: str) -> Optional[StabilityScore]:
        """Return the last cached score without recomputing."""
        raw = self._data.get(workflow_id, {}).get("score")
        if raw is None:
            return None
        return StabilityScore.from_dict(raw)

    def adjust_budget(self, workflow_id: str, base_budget: int) -> Tuple[int, str]:
        """
        Apply stability-based budget adjustment.

        Args:
            workflow_id: Workflow to look up.
            base_budget: Default token budget.

        Returns:
            (adjusted_budget, budget_tier) tuple.
        """
        score = self.get_cached_score(workflow_id)
        if score is None:
            score = self.score_workflow(workflow_id)
        adjusted = math.ceil(base_budget * score.budget_multiplier)
        return adjusted, score.budget_tier

    def all_scores(self) -> Dict[str, StabilityScore]:
        """Return cached scores for every tracked workflow."""
        result: Dict[str, StabilityScore] = {}
        for wf_id, entry in self._data.items():
            raw = entry.get("score")
            if raw:
                result[wf_id] = StabilityScore.from_dict(raw)
        return result

    def summary(self) -> str:
        """Human-readable summary of all tracked workflows."""
        scores = self.all_scores()
        if not scores:
            return "No workflows tracked yet."
        lines = ["Workflow Stability Summary", "=" * 40]
        for wf_id, s in sorted(scores.items()):
            lines.append(
                f"  {wf_id:<30}  score={s.score:.3f}  tier={s.budget_tier:<8}  "
                f"runs={s.run_count}  pass={s.pass_rate:.0%}"
            )
        return "\n".join(lines)
