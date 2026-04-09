# SPDX-License-Identifier: Apache-2.0
"""
Retrieval Quality Watchdog for TokenPak.

Monitors per-query retrieval health: chunk count, dedup rate, relevance
scores, source diversity, and chunk order stability. Compares each query
against a rolling 20-query baseline and fires alerts (and optional
auto-remediation) when drift exceeds configured thresholds.

Auto-remediation priority:
  1. Fix retrieval first (reindex, BM25 weights, filters)
  2. Only touch generation prompts after retrieval is stable
"""

from __future__ import annotations

import json
import logging
import statistics
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

_BASELINE_WINDOW = 20  # number of past queries to keep
_CHUNK_COUNT_GROWTH_PCT = 0.50  # alert when chunk count grows >50%
_DEDUP_RATE_DROP = 0.15  # alert when dedup rate drops by >15 pp
_IRRELEVANT_SOURCE_PCT = 0.30  # alert when irrelevant sources >30%
_ORDER_INSTABILITY_THRESHOLD = 0.40  # alert when rank correlation < 0.60


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class QueryRetrievalRecord:
    """Snapshot of retrieval metrics for a single query."""

    query_id: str
    """Unique identifier for this query (e.g. fingerprint)."""

    query_text: str
    """Raw query text."""

    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # --- chunk counts ---
    chunk_count: int = 0
    """Total chunks returned."""

    unique_chunk_count: int = 0
    """Unique (deduplicated) chunks."""

    # --- relevance ---
    relevance_scores: List[float] = field(default_factory=list)
    """Per-chunk relevance scores (0–1)."""

    # --- source diversity ---
    source_ids: List[str] = field(default_factory=list)
    """Source identifiers for each chunk (may repeat)."""

    # --- chunk order ---
    chunk_ids_ordered: List[str] = field(default_factory=list)
    """Chunk IDs in the order returned (for stability comparison)."""

    @property
    def dedup_rate(self) -> float:
        """Fraction of chunks that survived deduplication (higher = less duplication)."""
        if self.chunk_count == 0:
            return 1.0
        return self.unique_chunk_count / self.chunk_count

    @property
    def mean_relevance(self) -> float:
        """Mean relevance score across all chunks."""
        if not self.relevance_scores:
            return 0.0
        return statistics.mean(self.relevance_scores)

    @property
    def irrelevant_source_rate(self) -> float:
        """Fraction of chunks whose relevance is below 0.3 (proxy for irrelevance)."""
        if not self.relevance_scores:
            return 0.0
        irrelevant = sum(1 for s in self.relevance_scores if s < 0.3)
        return irrelevant / len(self.relevance_scores)

    @property
    def source_diversity(self) -> float:
        """Fraction of unique sources vs total chunks (higher = more diverse)."""
        if not self.source_ids:
            return 0.0
        return len(set(self.source_ids)) / len(self.source_ids)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QueryRetrievalRecord":
        return cls(**data)


@dataclass
class RetrievalBaseline:
    """Rolling aggregate of retrieval metrics over the last N queries."""

    mean_chunk_count: float = 0.0
    mean_dedup_rate: float = 1.0
    mean_relevance: float = 1.0
    mean_irrelevant_source_rate: float = 0.0
    mean_source_diversity: float = 1.0
    sample_size: int = 0


@dataclass
class RetrievalAlert:
    """Alert fired when retrieval quality drifts."""

    query_id: str
    timestamp: str
    dimensions: List[str]
    """Which dimensions regressed."""

    current: Dict[str, float]
    baseline: Dict[str, float]
    severity: str  # "warn" | "critical"
    remediation_applied: bool = False
    remediation_actions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Rank correlation helper (Spearman-like without scipy)
# ---------------------------------------------------------------------------


def _rank_correlation(a: Sequence[str], b: Sequence[str]) -> float:
    """
    Compute a simple overlap-based order correlation between two ID sequences.

    Returns 1.0 if identical order, 0.0 if completely different.
    Uses top-K overlap weighted by position.
    """
    if not a or not b:
        return 1.0
    k = min(len(a), len(b), 10)
    top_a = list(a[:k])
    top_b = list(b[:k])
    if top_a == top_b:
        return 1.0
    # Weighted intersection: items appearing in both lists, scored by positional agreement
    set_a = {item: idx for idx, item in enumerate(top_a)}
    set_b = {item: idx for idx, item in enumerate(top_b)}
    common = set(set_a.keys()) & set(set_b.keys())
    if not common:
        return 0.0
    # Score: 1 - mean(abs(rank_diff)) / k
    rank_diffs = [abs(set_a[item] - set_b[item]) for item in common]
    mean_diff = statistics.mean(rank_diffs)
    coverage = len(common) / k
    return coverage * (1 - mean_diff / k)


# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------


class RetrievalQualityWatchdog:
    """
    Monitor retrieval quality drift and optionally apply remediation.

    Usage::

        watchdog = RetrievalQualityWatchdog()
        record = QueryRetrievalRecord(
            query_id="q1",
            query_text="How does auth work?",
            chunk_count=12,
            unique_chunk_count=10,
            relevance_scores=[0.9, 0.7, 0.2, ...],
            source_ids=["a.py", "b.py", ...],
            chunk_ids_ordered=["c1", "c2", ...],
        )
        alert = watchdog.observe(record)
        if alert:
            print(alert)
    """

    def __init__(
        self,
        history_path: Optional[str] = None,
        baseline_window: int = _BASELINE_WINDOW,
        chunk_growth_threshold: float = _CHUNK_COUNT_GROWTH_PCT,
        dedup_drop_threshold: float = _DEDUP_RATE_DROP,
        irrelevant_source_threshold: float = _IRRELEVANT_SOURCE_PCT,
        order_instability_threshold: float = _ORDER_INSTABILITY_THRESHOLD,
        remediation_fn: Optional[Callable[[RetrievalAlert], List[str]]] = None,
        auto_remediate: bool = True,
    ):
        """
        Args:
            history_path: JSON file for persisting query history.
            baseline_window: Number of recent queries to use as baseline.
            chunk_growth_threshold: Alert if chunk count grows by this fraction.
            dedup_drop_threshold: Alert if dedup rate drops by this amount.
            irrelevant_source_threshold: Alert if irrelevant source fraction exceeds this.
            order_instability_threshold: Alert if rank correlation drops below (1 - threshold).
            remediation_fn: Optional callable to apply custom remediation. Receives the
                alert and returns a list of action strings taken.
            auto_remediate: Whether to apply default remediation when no custom fn given.
        """
        self.baseline_window = baseline_window
        self.chunk_growth_threshold = chunk_growth_threshold
        self.dedup_drop_threshold = dedup_drop_threshold
        self.irrelevant_source_threshold = irrelevant_source_threshold
        self.order_instability_threshold = order_instability_threshold
        self.remediation_fn = remediation_fn
        self.auto_remediate = auto_remediate

        self.history_path = Path(
            history_path or str(Path.home() / ".tokenpak" / "retrieval_watchdog_history.json")
        )
        self.history_path.parent.mkdir(parents=True, exist_ok=True)

        self._history: deque[QueryRetrievalRecord] = deque(maxlen=baseline_window)
        self._alerts: List[RetrievalAlert] = []
        self._load_history()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def observe(self, record: QueryRetrievalRecord) -> Optional[RetrievalAlert]:
        """
        Record a retrieval event and check for quality drift.

        Returns an alert if drift detected, otherwise None.
        """
        baseline = self._compute_baseline()
        alert: Optional[RetrievalAlert] = None

        if baseline.sample_size >= 3:
            alert = self._check_drift(record, baseline)

        # Append AFTER computing baseline so new record doesn't bias its own check
        self._history.append(record)
        self._save_history()

        if alert:
            if self.remediation_fn:
                actions = self.remediation_fn(alert)
                alert.remediation_actions = actions
                alert.remediation_applied = bool(actions)
            elif self.auto_remediate:
                actions = self._default_remediation(alert)
                alert.remediation_actions = actions
                alert.remediation_applied = bool(actions)

            self._alerts.append(alert)
            logger.warning(
                "Retrieval quality alert [%s] on query %s — dimensions: %s",
                alert.severity,
                alert.query_id,
                alert.dimensions,
            )

        return alert

    def get_baseline(self) -> RetrievalBaseline:
        """Return the current rolling baseline."""
        return self._compute_baseline()

    def get_alerts(self, last_n: Optional[int] = None) -> List[RetrievalAlert]:
        """Return recent alerts."""
        if last_n is None:
            return list(self._alerts)
        return list(self._alerts[-last_n:])

    def history(self) -> List[QueryRetrievalRecord]:
        """Return all records in the rolling window."""
        return list(self._history)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _compute_baseline(self) -> RetrievalBaseline:
        records = list(self._history)
        if not records:
            return RetrievalBaseline()
        return RetrievalBaseline(
            mean_chunk_count=statistics.mean(r.chunk_count for r in records),
            mean_dedup_rate=statistics.mean(r.dedup_rate for r in records),
            mean_relevance=statistics.mean(r.mean_relevance for r in records),
            mean_irrelevant_source_rate=statistics.mean(r.irrelevant_source_rate for r in records),
            mean_source_diversity=statistics.mean(r.source_diversity for r in records),
            sample_size=len(records),
        )

    def _check_drift(
        self, record: QueryRetrievalRecord, baseline: RetrievalBaseline
    ) -> Optional[RetrievalAlert]:
        dimensions: List[str] = []
        severity = "warn"

        # 1. Chunk count growth
        if baseline.mean_chunk_count > 0 and record.chunk_count > baseline.mean_chunk_count * (
            1 + self.chunk_growth_threshold
        ):
            dimensions.append("chunk_count_growth")
            severity = "critical"

        # 2. Dedup rate drop
        if record.dedup_rate < baseline.mean_dedup_rate - self.dedup_drop_threshold:
            dimensions.append("dedup_rate_drop")

        # 3. Irrelevant source rate exceeded
        if record.irrelevant_source_rate > self.irrelevant_source_threshold:
            dimensions.append("irrelevant_sources_high")
            severity = "critical"

        # 4. Chunk order instability (compare vs last query if available)
        last = list(self._history)[-1] if self._history else None
        if last and last.chunk_ids_ordered and record.chunk_ids_ordered:
            corr = _rank_correlation(record.chunk_ids_ordered, last.chunk_ids_ordered)
            if corr < (1 - self.order_instability_threshold):
                dimensions.append("chunk_order_instability")

        if not dimensions:
            return None

        return RetrievalAlert(
            query_id=record.query_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            dimensions=dimensions,
            current={
                "chunk_count": record.chunk_count,
                "dedup_rate": round(record.dedup_rate, 4),
                "mean_relevance": round(record.mean_relevance, 4),
                "irrelevant_source_rate": round(record.irrelevant_source_rate, 4),
                "source_diversity": round(record.source_diversity, 4),
            },
            baseline={
                "mean_chunk_count": round(baseline.mean_chunk_count, 2),
                "mean_dedup_rate": round(baseline.mean_dedup_rate, 4),
                "mean_relevance": round(baseline.mean_relevance, 4),
                "mean_irrelevant_source_rate": round(baseline.mean_irrelevant_source_rate, 4),
                "mean_source_diversity": round(baseline.mean_source_diversity, 4),
            },
            severity=severity,
        )

    def _default_remediation(self, alert: RetrievalAlert) -> List[str]:
        """
        Default remediation logic.

        Priority: Fix retrieval first (reindex, BM25, filters) before
        touching generation prompts.
        """
        actions: List[str] = []

        if "chunk_count_growth" in alert.dimensions:
            # Too many chunks → tighten top-K filter
            actions.append("tighten_top_k_filter: reduce max_chunks by 20%")
            logger.info("[remediation] Tightening top-K filter due to chunk_count_growth")

        if "dedup_rate_drop" in alert.dimensions:
            # Duplicates creeping in → strengthen dedup threshold
            actions.append("strengthen_dedup: lower similarity_threshold for dedup by 0.05")
            logger.info("[remediation] Strengthening dedup threshold due to dedup_rate_drop")

        if "irrelevant_sources_high" in alert.dimensions:
            # Bad sources entering context → adjust BM25 weights and trigger reindex
            actions.append("adjust_bm25_weights: increase k1 from 1.2→1.5, b from 0.75→0.85")
            actions.append("trigger_reindex: schedule background reindex for affected corpus")
            logger.warning(
                "[remediation] Adjusting BM25 weights + triggering reindex due to irrelevant_sources_high"
            )

        if "chunk_order_instability" in alert.dimensions:
            # Chunk ranking is unstable → tighten relevance filter cutoff
            actions.append("tighten_relevance_cutoff: raise min_relevance_score from 0.3→0.45")
            logger.info("[remediation] Raising relevance cutoff due to chunk_order_instability")

        if not actions:
            actions.append("no_action: drift below remediation threshold")

        return actions

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_history(self) -> None:
        if not self.history_path.exists():
            return
        try:
            with open(self.history_path) as f:
                raw = json.load(f)
            for entry in raw[-self.baseline_window :]:
                self._history.append(QueryRetrievalRecord.from_dict(entry))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load retrieval watchdog history: %s", exc)

    def _save_history(self) -> None:
        try:
            with open(self.history_path, "w") as f:
                json.dump([r.to_dict() for r in self._history], f, indent=2)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not save retrieval watchdog history: %s", exc)
