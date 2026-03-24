"""
A/B Auto-Optimizer — Module 7 (Pro+ feature).

Manages continuous A/B experiments for recipe/compression variants.
Tracks: token savings, quality score, latency.
Uses Welch's t-test (continuous) + chi-squared significance checks.
Auto-promotes winner at 95% confidence with ≥50 samples per variant.

Storage: SQLite (WAL mode) at ~/.tokenpak/ab_optimizer.db
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from scipy import stats as _scipy_stats

    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_DB_PATH = Path.home() / ".tokenpak" / "ab_optimizer.db"
MIN_SAMPLES = 50  # minimum samples per variant before significance check
CONFIDENCE_LEVEL = 0.95  # 95% confidence → α = 0.05
ALPHA = 1.0 - CONFIDENCE_LEVEL


class ExperimentStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PromotionAction(str, Enum):
    AUTO = "auto"  # system auto-promoted winner
    MANUAL = "manual"  # user forced promotion
    NONE = "none"  # no promotion (tie / inconclusive)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class VariantStats:
    """Running statistics for one variant."""

    name: str
    samples: int = 0
    # Token savings (ratio, higher is better)
    token_savings_sum: float = 0.0
    token_savings_sq_sum: float = 0.0
    # Quality score (0-1, higher is better)
    quality_sum: float = 0.0
    quality_sq_sum: float = 0.0
    # Latency (ms, lower is better)
    latency_sum: float = 0.0
    latency_sq_sum: float = 0.0

    @property
    def token_savings_mean(self) -> float:
        return self.token_savings_sum / self.samples if self.samples else 0.0

    @property
    def quality_mean(self) -> float:
        return self.quality_sum / self.samples if self.samples else 0.0

    @property
    def latency_mean(self) -> float:
        return self.latency_sum / self.samples if self.samples else 0.0

    def _variance(self, sum_: float, sq_sum_: float) -> float:
        if self.samples < 2:
            return 0.0
        mean = sum_ / self.samples
        # Welford variance: (sum_sq - n*mean²) / (n-1)
        var = (sq_sum_ - self.samples * mean**2) / (self.samples - 1)
        return max(var, 0.0)

    @property
    def token_savings_var(self) -> float:
        return self._variance(self.token_savings_sum, self.token_savings_sq_sum)

    @property
    def quality_var(self) -> float:
        return self._variance(self.quality_sum, self.quality_sq_sum)

    @property
    def latency_var(self) -> float:
        return self._variance(self.latency_sum, self.latency_sq_sum)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "samples": self.samples,
            "token_savings_mean": round(self.token_savings_mean, 4),
            "token_savings_var": round(self.token_savings_var, 6),
            "quality_mean": round(self.quality_mean, 4),
            "quality_var": round(self.quality_var, 6),
            "latency_mean_ms": round(self.latency_mean, 2),
            "latency_var": round(self.latency_var, 4),
        }


@dataclass
class SignificanceResult:
    """Result of significance test across all metrics."""

    significant: bool
    p_value_token_savings: float
    p_value_quality: float
    p_value_latency: float
    winner: Optional[str]  # variant name of winner, or None
    winner_metric: Optional[str]  # which metric drove the win
    composite_advantage: float  # % composite advantage of winner
    method: str = "welch_t_test"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "significant": self.significant,
            "p_values": {
                "token_savings": round(self.p_value_token_savings, 4),
                "quality": round(self.p_value_quality, 4),
                "latency": round(self.p_value_latency, 4),
            },
            "winner": self.winner,
            "winner_metric": self.winner_metric,
            "composite_advantage_pct": round(self.composite_advantage * 100, 2),
            "method": self.method,
        }


@dataclass
class Experiment:
    """An A/B experiment comparing two recipe variants."""

    id: str
    name: str
    description: str
    control_name: str
    treatment_name: str
    status: ExperimentStatus
    created_at: str
    completed_at: Optional[str]
    winner: Optional[str]
    promotion_action: PromotionAction
    manual_override: Optional[str]  # forced winner variant name
    tags: List[str]
    # Variant stats (serialized as JSON in DB)
    control_stats: VariantStats
    treatment_stats: VariantStats

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "control": self.control_stats.to_dict(),
            "treatment": self.treatment_stats.to_dict(),
            "status": self.status.value,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "winner": self.winner,
            "promotion_action": self.promotion_action.value,
            "manual_override": self.manual_override,
            "tags": self.tags,
        }


# ---------------------------------------------------------------------------
# Statistical tests
# ---------------------------------------------------------------------------


def _welch_t_pvalue(
    mean1: float,
    var1: float,
    n1: int,
    mean2: float,
    var2: float,
    n2: int,
) -> float:
    """
    Welch's t-test p-value (two-tailed).
    Returns 1.0 when either sample is too small or variance is zero.
    """
    if n1 < 2 or n2 < 2:
        return 1.0
    if var1 == 0 and var2 == 0:
        # identical distributions — not significant
        return 1.0 if mean1 == mean2 else 0.0

    se1 = var1 / n1
    se2 = var2 / n2
    se = math.sqrt(se1 + se2)
    if se == 0:
        return 1.0

    t_stat = (mean1 - mean2) / se

    # Welch-Satterthwaite degrees of freedom
    df = (se1 + se2) ** 2 / ((se1**2 / (n1 - 1)) + (se2**2 / (n2 - 1)))
    df = max(df, 1.0)

    if _HAS_SCIPY:
        p = float(_scipy_stats.t.sf(abs(t_stat), df) * 2)
    else:
        # Rough approximation via normal distribution for large df
        # P(|Z| > |t|) ≈ erfc(|t|/sqrt(2))
        p = math.erfc(abs(t_stat) / math.sqrt(2))

    return max(0.0, min(1.0, p))


def _chi_squared_pvalue(observed: List[int], expected: List[float]) -> float:
    """Simple chi-squared goodness-of-fit (k bins). Returns p-value."""
    if not observed or not expected:
        return 1.0
    if _HAS_SCIPY:
        chi2, p = _scipy_stats.chisquare(observed, expected)
        return float(p)
    # Manual approximation
    chi2 = sum((o - e) ** 2 / e for o, e in zip(observed, expected) if e > 0)
    len(observed) - 1
    # Rough p-value via survival function approximation (not precise)
    # Use scipy if available; otherwise best-effort
    return max(0.0, math.exp(-0.5 * chi2))  # very rough


def compute_significance(control: VariantStats, treatment: VariantStats) -> SignificanceResult:
    """
    Compare control vs treatment across all three metrics.
    Returns SignificanceResult indicating whether there's a clear winner.
    """
    n_c, n_t = control.samples, treatment.samples

    # p-values for each metric
    p_savings = _welch_t_pvalue(
        control.token_savings_mean,
        control.token_savings_var,
        n_c,
        treatment.token_savings_mean,
        treatment.token_savings_var,
        n_t,
    )
    p_quality = _welch_t_pvalue(
        control.quality_mean,
        control.quality_var,
        n_c,
        treatment.quality_mean,
        treatment.quality_var,
        n_t,
    )
    p_latency = _welch_t_pvalue(
        control.latency_mean,
        control.latency_var,
        n_c,
        treatment.latency_mean,
        treatment.latency_var,
        n_t,
    )

    # Determine significance on primary metric (token_savings)
    significant = p_savings < ALPHA and min(n_c, n_t) >= MIN_SAMPLES

    # Composite score: savings (40%), quality (40%), latency-inverted (20%)
    # Normalise latency: lower is better → invert as fraction
    max_lat = max(control.latency_mean, treatment.latency_mean, 1.0)
    c_lat_score = 1.0 - (control.latency_mean / max_lat)
    t_lat_score = 1.0 - (treatment.latency_mean / max_lat)

    c_composite = (
        0.40 * control.token_savings_mean + 0.40 * control.quality_mean + 0.20 * c_lat_score
    )
    t_composite = (
        0.40 * treatment.token_savings_mean + 0.40 * treatment.quality_mean + 0.20 * t_lat_score
    )

    if not significant:
        winner = None
        winner_metric = None
        composite_advantage = 0.0
    else:
        if t_composite > c_composite:
            winner = treatment.name
            composite_advantage = (t_composite - c_composite) / max(c_composite, 1e-9)
        elif c_composite > t_composite:
            winner = control.name
            composite_advantage = (c_composite - t_composite) / max(t_composite, 1e-9)
        else:
            winner = None
            composite_advantage = 0.0

        # Label the primary winning metric
        if winner:
            savings_delta = treatment.token_savings_mean - control.token_savings_mean
            quality_delta = treatment.quality_mean - control.quality_mean
            latency_delta = control.latency_mean - treatment.latency_mean  # inverted
            best = max(abs(savings_delta), abs(quality_delta), abs(latency_delta))
            if best == abs(savings_delta):
                winner_metric = "token_savings"
            elif best == abs(quality_delta):
                winner_metric = "quality"
            else:
                winner_metric = "latency"
        else:
            winner_metric = None

    return SignificanceResult(
        significant=significant,
        p_value_token_savings=p_savings,
        p_value_quality=p_quality,
        p_value_latency=p_latency,
        winner=winner,
        winner_metric=winner_metric,
        composite_advantage=composite_advantage,
    )


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    control_name   TEXT NOT NULL,
    treatment_name TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active',
    created_at   TEXT NOT NULL,
    completed_at TEXT,
    winner       TEXT,
    promotion_action TEXT NOT NULL DEFAULT 'none',
    manual_override  TEXT,
    tags         TEXT NOT NULL DEFAULT '[]',
    control_stats   TEXT NOT NULL DEFAULT '{}',
    treatment_stats TEXT NOT NULL DEFAULT '{}'
);
"""


class ABOptimizerStore:
    """Thread-safe SQLite-backed store for A/B experiments."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    def _stats_to_json(self, stats: VariantStats) -> str:
        return json.dumps(
            {
                "name": stats.name,
                "samples": stats.samples,
                "token_savings_sum": stats.token_savings_sum,
                "token_savings_sq_sum": stats.token_savings_sq_sum,
                "quality_sum": stats.quality_sum,
                "quality_sq_sum": stats.quality_sq_sum,
                "latency_sum": stats.latency_sum,
                "latency_sq_sum": stats.latency_sq_sum,
            }
        )

    def _json_to_stats(self, blob: str, default_name: str) -> VariantStats:
        d = json.loads(blob) if blob else {}
        return VariantStats(
            name=d.get("name", default_name),
            samples=d.get("samples", 0),
            token_savings_sum=d.get("token_savings_sum", 0.0),
            token_savings_sq_sum=d.get("token_savings_sq_sum", 0.0),
            quality_sum=d.get("quality_sum", 0.0),
            quality_sq_sum=d.get("quality_sq_sum", 0.0),
            latency_sum=d.get("latency_sum", 0.0),
            latency_sq_sum=d.get("latency_sq_sum", 0.0),
        )

    def _row_to_experiment(self, row: sqlite3.Row) -> Experiment:
        return Experiment(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            control_name=row["control_name"],
            treatment_name=row["treatment_name"],
            status=ExperimentStatus(row["status"]),
            created_at=row["created_at"],
            completed_at=row["completed_at"],
            winner=row["winner"],
            promotion_action=PromotionAction(row["promotion_action"]),
            manual_override=row["manual_override"],
            tags=json.loads(row["tags"]),
            control_stats=self._json_to_stats(row["control_stats"], row["control_name"]),
            treatment_stats=self._json_to_stats(row["treatment_stats"], row["treatment_name"]),
        )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_experiment(
        self,
        name: str,
        description: str = "",
        control_name: str = "control",
        treatment_name: str = "treatment",
        tags: Optional[List[str]] = None,
    ) -> Experiment:
        exp_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        control_stats = VariantStats(name=control_name)
        treatment_stats = VariantStats(name=treatment_name)
        exp = Experiment(
            id=exp_id,
            name=name,
            description=description,
            control_name=control_name,
            treatment_name=treatment_name,
            status=ExperimentStatus.ACTIVE,
            created_at=now,
            completed_at=None,
            winner=None,
            promotion_action=PromotionAction.NONE,
            manual_override=None,
            tags=tags or [],
            control_stats=control_stats,
            treatment_stats=treatment_stats,
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO experiments
                   (id, name, description, control_name, treatment_name,
                    status, created_at, completed_at, winner, promotion_action,
                    manual_override, tags, control_stats, treatment_stats)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    exp.id,
                    exp.name,
                    exp.description,
                    exp.control_name,
                    exp.treatment_name,
                    exp.status.value,
                    exp.created_at,
                    None,
                    None,
                    PromotionAction.NONE.value,
                    None,
                    json.dumps(exp.tags),
                    self._stats_to_json(control_stats),
                    self._stats_to_json(treatment_stats),
                ),
            )
        return exp

    def get_experiment(self, exp_id: str) -> Optional[Experiment]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM experiments WHERE id = ?", (exp_id,)).fetchone()
        return self._row_to_experiment(row) if row else None

    def list_experiments(
        self,
        status_filter: Optional[str] = None,
    ) -> List[Experiment]:
        with self._lock, self._connect() as conn:
            if status_filter:
                rows = conn.execute(
                    "SELECT * FROM experiments WHERE status = ? ORDER BY created_at DESC",
                    (status_filter,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM experiments ORDER BY created_at DESC").fetchall()
        return [self._row_to_experiment(r) for r in rows]

    def record_observation(
        self,
        exp_id: str,
        variant: str,
        token_savings: float,
        quality_score: float,
        latency_ms: float,
    ) -> Optional[SignificanceResult]:
        """
        Record one observation for a variant.
        Returns SignificanceResult if min_samples met, else None.
        Automatically promotes winner when significance reached.
        """
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM experiments WHERE id = ?", (exp_id,)).fetchone()
            if not row:
                raise ValueError(f"Experiment {exp_id} not found")
            exp = self._row_to_experiment(row)

            if exp.status != ExperimentStatus.ACTIVE:
                raise ValueError(f"Experiment {exp_id} is {exp.status.value}, not active")
            if variant not in (exp.control_name, exp.treatment_name):
                raise ValueError(
                    f"Unknown variant '{variant}'. "
                    f"Expected '{exp.control_name}' or '{exp.treatment_name}'"
                )

            # Update running stats
            stats = exp.control_stats if variant == exp.control_name else exp.treatment_stats
            stats.samples += 1
            stats.token_savings_sum += token_savings
            stats.token_savings_sq_sum += token_savings**2
            stats.quality_sum += quality_score
            stats.quality_sq_sum += quality_score**2
            stats.latency_sum += latency_ms
            stats.latency_sq_sum += latency_ms**2

            # Persist updated stats
            ctrl_json = self._stats_to_json(exp.control_stats)
            trt_json = self._stats_to_json(exp.treatment_stats)
            conn.execute(
                "UPDATE experiments SET control_stats=?, treatment_stats=? WHERE id=?",
                (ctrl_json, trt_json, exp_id),
            )

            # Check significance if enough samples
            min_n = min(exp.control_stats.samples, exp.treatment_stats.samples)
            if min_n < MIN_SAMPLES:
                return None

            result = compute_significance(exp.control_stats, exp.treatment_stats)

            if result.significant and result.winner and exp.manual_override is None:
                # Auto-promote winner
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    """UPDATE experiments
                       SET status=?, completed_at=?, winner=?, promotion_action=?
                       WHERE id=?""",
                    (
                        ExperimentStatus.COMPLETED.value,
                        now,
                        result.winner,
                        PromotionAction.AUTO.value,
                        exp_id,
                    ),
                )

            return result

    def force_winner(self, exp_id: str, variant: str) -> Experiment:
        """Manual override: force a variant as the winner."""
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM experiments WHERE id = ?", (exp_id,)).fetchone()
            if not row:
                raise ValueError(f"Experiment {exp_id} not found")
            exp = self._row_to_experiment(row)
            if variant not in (exp.control_name, exp.treatment_name):
                raise ValueError(f"Unknown variant '{variant}'")

            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """UPDATE experiments
                   SET status=?, completed_at=?, winner=?, promotion_action=?,
                       manual_override=?
                   WHERE id=?""",
                (
                    ExperimentStatus.COMPLETED.value,
                    now,
                    variant,
                    PromotionAction.MANUAL.value,
                    variant,
                    exp_id,
                ),
            )
        return self.get_experiment(exp_id)  # type: ignore[return-value]

    def cancel_experiment(self, exp_id: str) -> Experiment:
        """Cancel an active experiment."""
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM experiments WHERE id = ?", (exp_id,)).fetchone()
            if not row:
                raise ValueError(f"Experiment {exp_id} not found")
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """UPDATE experiments SET status=?, completed_at=? WHERE id=?""",
                (ExperimentStatus.CANCELLED.value, now, exp_id),
            )
        return self.get_experiment(exp_id)  # type: ignore[return-value]

    def get_results(self, exp_id: str) -> Dict[str, Any]:
        """Full results for an experiment including significance test."""
        exp = self.get_experiment(exp_id)
        if not exp:
            raise ValueError(f"Experiment {exp_id} not found")

        sig = compute_significance(exp.control_stats, exp.treatment_stats)
        result = exp.to_dict()
        result["significance"] = sig.to_dict()
        result["min_samples_met"] = (
            min(exp.control_stats.samples, exp.treatment_stats.samples) >= MIN_SAMPLES
        )
        result["min_samples_required"] = MIN_SAMPLES
        result["confidence_level"] = CONFIDENCE_LEVEL
        return result
