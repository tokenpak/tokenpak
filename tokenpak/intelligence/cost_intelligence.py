"""Cost Intelligence Module (Module 6) — Pro+ feature.

Analyzes anonymized usage metrics to provide:
- Daily/weekly/monthly cost trends
- Per-model cost breakdown + compression savings
- Anomaly detection (spikes > 2× baseline)
- 7d/30d cost projections (linear regression or average)
- Model switch recommendations
- Budget alerts (warn 80%, critical 95%)

All endpoints require Pro+ license tier.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Model pricing table (per 1M tokens, USD)
# ---------------------------------------------------------------------------

MODEL_COSTS: Dict[str, Dict[str, float]] = {
    "claude-opus-4-5": {"input": 15.00, "output": 75.00},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-haiku-3-5": {"input": 0.25, "output": 1.25},
    "claude-haiku-4-5": {"input": 0.25, "output": 1.25},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gemini-2-flash": {"input": 0.075, "output": 0.30},
    "gemini-pro": {"input": 1.25, "output": 5.00},
    "codex": {"input": 3.00, "output": 12.00},
    "_fallback": {"input": 1.00, "output": 3.00},
}

# Cheaper alternatives for recommendations: model → [(alt_model, reason), ...]
MODEL_ALTERNATIVES: Dict[str, List[Tuple[str, str]]] = {
    "claude-opus-4-5": [
        ("claude-sonnet-4-5", "80% cheaper, similar quality for most tasks"),
        ("claude-haiku-3-5", "95% cheaper, best for simple tasks"),
    ],
    "claude-opus-4-6": [
        ("claude-sonnet-4-6", "80% cheaper, similar quality for most tasks"),
        ("claude-haiku-4-5", "95% cheaper, best for simple tasks"),
    ],
    "claude-sonnet-4-5": [
        ("claude-haiku-3-5", "75% cheaper, great for structured/simple tasks"),
        ("gpt-4o-mini", "85% cheaper, suitable for lightweight prompts"),
    ],
    "claude-sonnet-4-6": [
        ("claude-haiku-4-5", "75% cheaper, great for structured/simple tasks"),
        ("gpt-4o-mini", "85% cheaper, suitable for lightweight prompts"),
    ],
    "gpt-4o": [
        ("gpt-4o-mini", "90% cheaper, good for simple tasks"),
        ("gemini-2-flash", "97% cheaper, fast responses"),
    ],
    "gemini-pro": [
        ("gemini-2-flash", "90% cheaper, fast responses"),
    ],
    "codex": [
        ("claude-haiku-3-5", "75% cheaper, good for structured code tasks"),
    ],
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class DailyMetric:
    """One day of aggregated cost + usage data."""

    date_utc: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    tokens_saved: int = 0
    requests: int = 0
    model: str = ""  # primary model used that day


@dataclass
class Trend:
    """Period trend summary."""

    period: str  # daily / weekly / monthly
    total_cost_usd: float
    avg_daily_cost_usd: float
    total_tokens: int
    total_tokens_saved: int
    avg_compression_ratio: float
    days_covered: int


@dataclass
class Anomaly:
    """Detected cost spike."""

    date_utc: str
    cost_usd: float
    baseline_usd: float
    spike_ratio: float
    message: str


@dataclass
class Projection:
    """Cost projection for a future window."""

    period_days: int
    projected_cost_usd: float
    confidence: str  # low / medium / high
    based_on_days: int
    method: str  # linear / average


@dataclass
class ModelRecommendation:
    """Model switch recommendation."""

    current_model: str
    recommended_model: str
    reason: str
    estimated_savings_pct: float
    monthly_savings_usd: float


@dataclass
class BudgetAlert:
    """Budget usage alert."""

    level: str  # ok / warn / critical
    budget_usd: float
    spent_usd: float
    pct_used: float
    message: str


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------


class CostIntelligence:
    """Stateless cost intelligence analysis engine."""

    # ── Trends ─────────────────────────────────────────────────────────────

    @staticmethod
    def compute_trends(metrics: List[DailyMetric]) -> Dict[str, Trend]:
        """Compute daily, weekly, and monthly trends."""

        def _empty(period):
            return Trend(period, 0.0, 0.0, 0, 0, 0.0, 0)

        if not metrics:
            return {p: _empty(p) for p in ("daily", "weekly", "monthly")}

        today = date.today()

        def _subset(days: int) -> List[DailyMetric]:
            cutoff = (today - timedelta(days=days - 1)).isoformat()
            return [m for m in metrics if m.date_utc >= cutoff]

        def _trend(period: str, days: int) -> Trend:
            subset = _subset(days)
            total_cost = sum(m.cost_usd for m in subset)
            total_input = sum(m.input_tokens for m in subset)
            total_tokens = total_input + sum(m.output_tokens for m in subset)
            total_saved = sum(m.tokens_saved for m in subset)
            days_covered = len({m.date_utc for m in subset})
            avg_daily = total_cost / max(1, days_covered)
            # Average compression ratio across days that have input tokens
            comp_days = [m for m in subset if m.input_tokens > 0]
            avg_compression = (
                sum(m.tokens_saved / m.input_tokens for m in comp_days) / len(comp_days)
                if comp_days
                else 0.0
            )
            return Trend(
                period=period,
                total_cost_usd=round(total_cost, 6),
                avg_daily_cost_usd=round(avg_daily, 6),
                total_tokens=total_tokens,
                total_tokens_saved=total_saved,
                avg_compression_ratio=round(avg_compression, 4),
                days_covered=days_covered,
            )

        return {
            "daily": _trend("daily", 1),
            "weekly": _trend("weekly", 7),
            "monthly": _trend("monthly", 30),
        }

    # ── Model breakdown ─────────────────────────────────────────────────────

    @staticmethod
    def compute_model_breakdown(metrics: List[DailyMetric]) -> List[dict]:
        """Per-model cost and compression breakdown."""
        totals: Dict[str, dict] = {}
        for m in metrics:
            key = m.model or "_unknown"
            if key not in totals:
                totals[key] = {
                    "model": key,
                    "cost_usd": 0.0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "tokens_saved": 0,
                    "requests": 0,
                    "days": 0,
                }
            t = totals[key]
            t["cost_usd"] += m.cost_usd
            t["input_tokens"] += m.input_tokens
            t["output_tokens"] += m.output_tokens
            t["tokens_saved"] += m.tokens_saved
            t["requests"] += m.requests
            t["days"] += 1

        grand_total = sum(t["cost_usd"] for t in totals.values()) or 1e-9
        result = []
        for t in sorted(totals.values(), key=lambda x: x["cost_usd"], reverse=True):
            t["cost_usd"] = round(t["cost_usd"], 6)
            t["pct_of_total"] = round(t["cost_usd"] / grand_total * 100, 1)
            inp = t["input_tokens"]
            t["avg_compression_ratio"] = round(t["tokens_saved"] / inp, 4) if inp > 0 else 0.0
            result.append(t)
        return result

    # ── Anomaly detection ───────────────────────────────────────────────────

    @staticmethod
    def detect_anomalies(
        metrics: List[DailyMetric],
        threshold: float = 2.0,
    ) -> List[Anomaly]:
        """Detect days where cost exceeds threshold × rolling 7-day baseline."""
        if len(metrics) < 3:
            return []

        sorted_m = sorted(metrics, key=lambda m: m.date_utc)
        anomalies: List[Anomaly] = []

        for i, m in enumerate(sorted_m):
            window = sorted_m[max(0, i - 7) : i]
            if not window:
                continue
            baseline = sum(w.cost_usd for w in window) / len(window)
            if baseline <= 0:
                continue
            ratio = m.cost_usd / baseline
            if ratio >= threshold:
                anomalies.append(
                    Anomaly(
                        date_utc=m.date_utc,
                        cost_usd=round(m.cost_usd, 6),
                        baseline_usd=round(baseline, 6),
                        spike_ratio=round(ratio, 2),
                        message=(
                            f"Cost spike {ratio:.1f}× baseline on {m.date_utc} "
                            f"(${m.cost_usd:.4f} vs ${baseline:.4f} avg)"
                        ),
                    )
                )
        return anomalies

    # ── Projections ─────────────────────────────────────────────────────────

    @staticmethod
    def compute_projections(metrics: List[DailyMetric]) -> Dict[str, Projection]:
        """Compute 7d and 30d cost projections."""

        def _empty(d):
            return Projection(d, 0.0, "low", 0, "average")

        if not metrics:
            return {"7d": _empty(7), "30d": _empty(30)}

        sorted_m = sorted(metrics, key=lambda m: m.date_utc)
        n = len(sorted_m)
        costs = [m.cost_usd for m in sorted_m]

        def _project(days: int) -> Projection:
            if n >= 5:
                # Ordinary least-squares linear regression
                xs = list(range(n))
                mean_x = sum(xs) / n
                mean_y = sum(costs) / n
                ss_xx = sum((x - mean_x) ** 2 for x in xs) or 1e-10
                ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, costs))
                slope = ss_xy / ss_xx
                intercept = mean_y - slope * mean_x
                # Predict average daily rate over the future window
                mid_x = n + days / 2
                daily_rate = max(0.0, intercept + slope * mid_x)
                projected = daily_rate * days
                confidence = "high" if n >= 14 else "medium"
                method = "linear"
            else:
                avg_daily = sum(costs) / max(1, n)
                projected = avg_daily * days
                confidence = "low"
                method = "average"

            return Projection(
                period_days=days,
                projected_cost_usd=round(max(0.0, projected), 4),
                confidence=confidence,
                based_on_days=n,
                method=method,
            )

        return {"7d": _project(7), "30d": _project(30)}

    # ── Recommendations ─────────────────────────────────────────────────────

    @staticmethod
    def compute_recommendations(
        model_breakdown: List[dict],
        monthly_budget_usd: Optional[float] = None,
    ) -> List[ModelRecommendation]:
        """Generate model-switch recommendations from usage patterns."""
        recommendations: List[ModelRecommendation] = []

        for entry in model_breakdown:
            model = entry.get("model", "")
            # Annualise to monthly cost
            days = max(1, entry.get("days", 1))
            monthly_cost = entry.get("cost_usd", 0.0) * 30.0 / days

            for alt_model, reason in MODEL_ALTERNATIVES.get(model, []):
                curr = MODEL_COSTS.get(model, MODEL_COSTS["_fallback"])
                alt = MODEL_COSTS.get(alt_model, MODEL_COSTS["_fallback"])
                curr_blended = (curr["input"] + curr["output"]) / 2
                alt_blended = (alt["input"] + alt["output"]) / 2

                if curr_blended <= 0 or alt_blended >= curr_blended:
                    continue  # alt is not cheaper

                savings_pct = round((1 - alt_blended / curr_blended) * 100, 1)
                monthly_savings = round(monthly_cost * savings_pct / 100, 2)

                if savings_pct >= 20:  # only surface meaningful savings
                    recommendations.append(
                        ModelRecommendation(
                            current_model=model,
                            recommended_model=alt_model,
                            reason=reason,
                            estimated_savings_pct=savings_pct,
                            monthly_savings_usd=monthly_savings,
                        )
                    )

        # Highest monthly savings first
        recommendations.sort(key=lambda r: r.monthly_savings_usd, reverse=True)
        return recommendations

    # ── Budget alerts ───────────────────────────────────────────────────────

    @staticmethod
    def check_budget_alert(spent_usd: float, budget_usd: float) -> BudgetAlert:
        """Return alert level based on % of budget consumed."""
        if budget_usd <= 0:
            return BudgetAlert(
                level="ok",
                budget_usd=budget_usd,
                spent_usd=round(spent_usd, 4),
                pct_used=0.0,
                message="No budget configured",
            )

        pct = round(spent_usd / budget_usd * 100, 1)
        if pct >= 95:
            level = "critical"
            message = (
                f"CRITICAL: {pct:.1f}% of budget consumed (${spent_usd:.2f} / ${budget_usd:.2f})"
            )
        elif pct >= 80:
            level = "warn"
            message = (
                f"WARNING: {pct:.1f}% of budget consumed (${spent_usd:.2f} / ${budget_usd:.2f})"
            )
        else:
            level = "ok"
            message = f"OK: {pct:.1f}% of budget consumed (${spent_usd:.2f} / ${budget_usd:.2f})"

        return BudgetAlert(
            level=level,
            budget_usd=round(budget_usd, 4),
            spent_usd=round(spent_usd, 4),
            pct_used=pct,
            message=message,
        )

    # ── Full analysis ───────────────────────────────────────────────────────

    @classmethod
    def analyze(
        cls,
        metrics: List[DailyMetric],
        monthly_budget_usd: Optional[float] = None,
        anomaly_threshold: float = 2.0,
    ) -> dict:
        """Run full analysis pipeline and return a combined result dict."""
        trends = cls.compute_trends(metrics)
        model_breakdown = cls.compute_model_breakdown(metrics)
        anomalies = cls.detect_anomalies(metrics, threshold=anomaly_threshold)
        projections = cls.compute_projections(metrics)
        recommendations = cls.compute_recommendations(
            model_breakdown, monthly_budget_usd=monthly_budget_usd
        )

        # Budget alert on 30-day projection (or actual monthly spend)
        monthly_spent = trends["monthly"].total_cost_usd
        alert = cls.check_budget_alert(monthly_spent, monthly_budget_usd or 0.0)

        return {
            "trends": {k: asdict(v) for k, v in trends.items()},
            "model_breakdown": model_breakdown,
            "anomalies": [asdict(a) for a in anomalies],
            "projections": {k: asdict(v) for k, v in projections.items()},
            "recommendations": [asdict(r) for r in recommendations],
            "budget_alert": asdict(alert),
        }
