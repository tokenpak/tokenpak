"""Tests for tokenpak.intelligence.cost_intelligence and cost_router.

Coverage:
- Trends: daily/weekly/monthly
- Model breakdown: cost totals, percentages, compression
- Anomaly detection: spike ratio, threshold, edge cases
- Projections: 7d/30d (linear + average), confidence levels
- Recommendations: model alternatives, savings filtering
- Budget alerts: ok/warn/critical thresholds
- API endpoints: analyze, projections, recommendations (Pro+ guard)
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "tokenpak.intelligence.cost_intelligence", reason="module not available in current build"
)
from datetime import date, timedelta
from typing import List

import pytest
from fastapi.testclient import TestClient
from tokenpak.intelligence.auth import APIKeyValidator, LicenseTier
from tokenpak.intelligence.cost_intelligence import (
    CostIntelligence,
    DailyMetric,
)
from tokenpak.intelligence.server import create_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_metrics(
    n: int,
    base_cost: float = 1.0,
    model: str = "claude-sonnet-4-5",
    input_tokens: int = 10_000,
    output_tokens: int = 2_000,
    tokens_saved: int = 1_000,
) -> List[DailyMetric]:
    today = date.today()
    return [
        DailyMetric(
            date_utc=(today - timedelta(days=n - 1 - i)).isoformat(),
            cost_usd=base_cost,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tokens_saved=tokens_saved,
            requests=10,
            model=model,
        )
        for i in range(n)
    ]


def _make_client(tier: LicenseTier = LicenseTier.PRO):
    validator = APIKeyValidator()
    validator.register("test-key", tier)
    app = create_app(validator=validator)
    client = TestClient(app, raise_server_exceptions=False)
    return client, "test-key"


# ===========================================================================
# CostIntelligence — unit tests
# ===========================================================================


class TestTrends:
    def test_empty_metrics_returns_zero_trends(self):
        trends = CostIntelligence.compute_trends([])
        for period in ("daily", "weekly", "monthly"):
            t = trends[period]
            assert t.total_cost_usd == 0.0
            assert t.avg_daily_cost_usd == 0.0
            assert t.days_covered == 0

    def test_single_day_daily_trend(self):
        metrics = _make_metrics(1, base_cost=5.0)
        trends = CostIntelligence.compute_trends(metrics)
        assert trends["daily"].total_cost_usd == pytest.approx(5.0)
        assert trends["daily"].days_covered == 1

    def test_weekly_trend_sums_7_days(self):
        metrics = _make_metrics(7, base_cost=2.0)
        trends = CostIntelligence.compute_trends(metrics)
        assert trends["weekly"].total_cost_usd == pytest.approx(14.0)
        assert trends["weekly"].avg_daily_cost_usd == pytest.approx(2.0)

    def test_monthly_trend_includes_30_days(self):
        metrics = _make_metrics(30, base_cost=1.0)
        trends = CostIntelligence.compute_trends(metrics)
        assert trends["monthly"].total_cost_usd == pytest.approx(30.0)
        assert trends["monthly"].days_covered == 30

    def test_compression_ratio_averaged(self):
        # 1000 saved / 10000 input = 0.1 per day
        metrics = _make_metrics(3, input_tokens=10_000, tokens_saved=1_000)
        trends = CostIntelligence.compute_trends(metrics)
        assert trends["weekly"].avg_compression_ratio == pytest.approx(0.1)

    def test_older_metrics_excluded_from_daily(self):
        """Metrics older than 1 day should not appear in daily trend."""
        metrics = _make_metrics(7, base_cost=3.0)
        trends = CostIntelligence.compute_trends(metrics)
        # Daily should only be today's metric
        assert trends["daily"].total_cost_usd == pytest.approx(3.0)
        assert trends["daily"].days_covered == 1


class TestModelBreakdown:
    def test_single_model(self):
        metrics = _make_metrics(3, base_cost=4.0, model="gpt-4o")
        breakdown = CostIntelligence.compute_model_breakdown(metrics)
        assert len(breakdown) == 1
        assert breakdown[0]["model"] == "gpt-4o"
        assert breakdown[0]["cost_usd"] == pytest.approx(12.0)
        assert breakdown[0]["pct_of_total"] == pytest.approx(100.0)

    def test_multiple_models_sorted_by_cost(self):
        today = date.today()
        metrics = [
            DailyMetric(today.isoformat(), 10.0, 5000, 1000, model="claude-opus-4-5"),
            DailyMetric((today - timedelta(1)).isoformat(), 2.0, 1000, 500, model="gemini-2-flash"),
        ]
        breakdown = CostIntelligence.compute_model_breakdown(metrics)
        assert breakdown[0]["model"] == "claude-opus-4-5"
        assert breakdown[1]["model"] == "gemini-2-flash"

    def test_percentage_sums_to_100(self):
        today = date.today()
        metrics = [
            DailyMetric(today.isoformat(), 3.0, 1000, 500, model="modelA"),
            DailyMetric((today - timedelta(1)).isoformat(), 7.0, 2000, 800, model="modelB"),
        ]
        breakdown = CostIntelligence.compute_model_breakdown(metrics)
        total_pct = sum(b["pct_of_total"] for b in breakdown)
        assert total_pct == pytest.approx(100.0, abs=0.2)

    def test_compression_ratio_in_breakdown(self):
        metrics = _make_metrics(1, input_tokens=10_000, tokens_saved=2_000)
        breakdown = CostIntelligence.compute_model_breakdown(metrics)
        assert breakdown[0]["avg_compression_ratio"] == pytest.approx(0.2)

    def test_empty_metrics(self):
        breakdown = CostIntelligence.compute_model_breakdown([])
        assert breakdown == []


class TestAnomalyDetection:
    def test_no_anomaly_when_stable(self):
        metrics = _make_metrics(10, base_cost=1.0)
        anomalies = CostIntelligence.detect_anomalies(metrics)
        assert anomalies == []

    def test_detects_spike(self):
        today = date.today()
        metrics = [
            DailyMetric((today - timedelta(days=9 - i)).isoformat(), 1.0, 1000, 200)
            for i in range(9)
        ]
        # Spike on final day: 5× baseline
        metrics.append(DailyMetric(today.isoformat(), 5.0, 1000, 200))
        anomalies = CostIntelligence.detect_anomalies(metrics, threshold=2.0)
        assert len(anomalies) == 1
        assert anomalies[0].date_utc == today.isoformat()
        assert anomalies[0].spike_ratio >= 2.0

    def test_no_anomaly_below_threshold(self):
        today = date.today()
        metrics = [
            DailyMetric((today - timedelta(days=9 - i)).isoformat(), 1.0, 1000, 200)
            for i in range(9)
        ]
        metrics.append(DailyMetric(today.isoformat(), 1.5, 1000, 200))  # 1.5× — under 2×
        anomalies = CostIntelligence.detect_anomalies(metrics, threshold=2.0)
        assert anomalies == []

    def test_insufficient_data_returns_empty(self):
        metrics = _make_metrics(2)
        assert CostIntelligence.detect_anomalies(metrics) == []

    def test_custom_threshold(self):
        today = date.today()
        metrics = [
            DailyMetric((today - timedelta(days=9 - i)).isoformat(), 1.0, 1000, 200)
            for i in range(9)
        ]
        metrics.append(DailyMetric(today.isoformat(), 3.5, 1000, 200))
        # Should NOT flag with threshold=4.0
        assert CostIntelligence.detect_anomalies(metrics, threshold=4.0) == []
        # Should flag with threshold=3.0
        assert len(CostIntelligence.detect_anomalies(metrics, threshold=3.0)) == 1


class TestProjections:
    def test_empty_returns_zero_projection(self):
        proj = CostIntelligence.compute_projections([])
        assert proj["7d"].projected_cost_usd == 0.0
        assert proj["30d"].projected_cost_usd == 0.0

    def test_average_method_with_few_points(self):
        metrics = _make_metrics(3, base_cost=2.0)
        proj = CostIntelligence.compute_projections(metrics)
        assert proj["7d"].method == "average"
        assert proj["7d"].projected_cost_usd == pytest.approx(14.0)
        assert proj["30d"].projected_cost_usd == pytest.approx(60.0)

    def test_linear_method_with_sufficient_data(self):
        metrics = _make_metrics(14, base_cost=1.0)
        proj = CostIntelligence.compute_projections(metrics)
        assert proj["7d"].method == "linear"
        assert proj["7d"].confidence in ("medium", "high")

    def test_high_confidence_with_14_plus_days(self):
        metrics = _make_metrics(14, base_cost=1.0)
        proj = CostIntelligence.compute_projections(metrics)
        assert proj["7d"].confidence == "high"

    def test_projection_non_negative(self):
        """Decreasing trend should not produce negative projections."""
        today = date.today()
        costs = [10.0, 8.0, 6.0, 4.0, 2.0]  # steep decline
        metrics = [
            DailyMetric((today - timedelta(days=len(costs) - 1 - i)).isoformat(), c, 1000, 200)
            for i, c in enumerate(costs)
        ]
        proj = CostIntelligence.compute_projections(metrics)
        assert proj["7d"].projected_cost_usd >= 0.0
        assert proj["30d"].projected_cost_usd >= 0.0

    def test_based_on_days_accurate(self):
        metrics = _make_metrics(10)
        proj = CostIntelligence.compute_projections(metrics)
        assert proj["7d"].based_on_days == 10


class TestRecommendations:
    def test_opus_gets_cheaper_alternatives(self):
        breakdown = [
            {
                "model": "claude-opus-4-5",
                "cost_usd": 100.0,
                "days": 30,
                "input_tokens": 1000,
                "output_tokens": 500,
                "tokens_saved": 0,
                "requests": 10,
            }
        ]
        recs = CostIntelligence.compute_recommendations(breakdown)
        models = [r.recommended_model for r in recs]
        assert any("haiku" in m or "sonnet" in m for m in models)
        for r in recs:
            assert r.estimated_savings_pct >= 20

    def test_gemini_flash_no_recommendations(self):
        """gemini-2-flash is already the cheapest — no alternatives."""
        breakdown = [
            {
                "model": "gemini-2-flash",
                "cost_usd": 5.0,
                "days": 30,
                "input_tokens": 1000,
                "output_tokens": 500,
                "tokens_saved": 0,
                "requests": 5,
            }
        ]
        recs = CostIntelligence.compute_recommendations(breakdown)
        assert recs == []

    def test_recs_sorted_by_monthly_savings(self):
        breakdown = [
            {
                "model": "claude-opus-4-5",
                "cost_usd": 500.0,
                "days": 30,
                "input_tokens": 1000,
                "output_tokens": 500,
                "tokens_saved": 0,
                "requests": 50,
            }
        ]
        recs = CostIntelligence.compute_recommendations(breakdown)
        for i in range(len(recs) - 1):
            assert recs[i].monthly_savings_usd >= recs[i + 1].monthly_savings_usd

    def test_empty_breakdown(self):
        assert CostIntelligence.compute_recommendations([]) == []

    def test_unknown_model_no_crash(self):
        breakdown = [
            {
                "model": "totally-unknown-model-xyz",
                "cost_usd": 10.0,
                "days": 30,
                "input_tokens": 1000,
                "output_tokens": 500,
                "tokens_saved": 0,
                "requests": 5,
            }
        ]
        recs = CostIntelligence.compute_recommendations(breakdown)
        # No alternatives defined → empty list, no crash
        assert recs == []


class TestBudgetAlerts:
    def test_ok_below_80_percent(self):
        alert = CostIntelligence.check_budget_alert(70.0, 100.0)
        assert alert.level == "ok"
        assert alert.pct_used == pytest.approx(70.0)

    def test_warn_at_80_percent(self):
        alert = CostIntelligence.check_budget_alert(80.0, 100.0)
        assert alert.level == "warn"

    def test_warn_between_80_and_95(self):
        alert = CostIntelligence.check_budget_alert(90.0, 100.0)
        assert alert.level == "warn"

    def test_critical_at_95_percent(self):
        alert = CostIntelligence.check_budget_alert(95.0, 100.0)
        assert alert.level == "critical"

    def test_critical_over_100_percent(self):
        alert = CostIntelligence.check_budget_alert(120.0, 100.0)
        assert alert.level == "critical"
        assert alert.pct_used == pytest.approx(120.0)

    def test_no_budget_configured(self):
        alert = CostIntelligence.check_budget_alert(50.0, 0.0)
        assert alert.level == "ok"
        assert "No budget" in alert.message

    def test_alert_contains_dollar_amounts(self):
        alert = CostIntelligence.check_budget_alert(85.0, 100.0)
        assert "85.00" in alert.message or "85.0" in alert.message


class TestFullAnalysis:
    def test_analyze_returns_all_keys(self):
        metrics = _make_metrics(14)
        result = CostIntelligence.analyze(metrics, monthly_budget_usd=50.0)
        assert "trends" in result
        assert "model_breakdown" in result
        assert "anomalies" in result
        assert "projections" in result
        assert "recommendations" in result
        assert "budget_alert" in result

    def test_analyze_with_spike(self):
        today = date.today()
        metrics = [
            DailyMetric((today - timedelta(days=9 - i)).isoformat(), 1.0, 1000, 200)
            for i in range(9)
        ]
        metrics.append(DailyMetric(today.isoformat(), 10.0, 1000, 200))
        result = CostIntelligence.analyze(metrics)
        assert len(result["anomalies"]) >= 1

    def test_analyze_empty_metrics(self):
        result = CostIntelligence.analyze([])
        assert result["trends"]["daily"]["total_cost_usd"] == 0.0
        assert result["model_breakdown"] == []
        assert result["anomalies"] == []


# ===========================================================================
# API endpoint tests
# ===========================================================================


class TestCostAnalyzeEndpoint:
    def _payload(self, n: int = 7, cost: float = 1.0):
        today = date.today()
        return {
            "metrics": [
                {
                    "date_utc": (today - timedelta(days=n - 1 - i)).isoformat(),
                    "cost_usd": cost,
                    "input_tokens": 10000,
                    "output_tokens": 2000,
                    "tokens_saved": 1000,
                    "requests": 5,
                    "model": "claude-sonnet-4-5",
                }
                for i in range(n)
            ],
            "monthly_budget_usd": 50.0,
        }

    def test_pro_key_succeeds(self):
        client, key = _make_client(LicenseTier.PRO)
        resp = client.post(
            "/v1/cost/analyze",
            json=self._payload(),
            headers={"X-TokenPak-Key": key},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "trends" in data
        assert "model_breakdown" in data
        assert "request_id" in data

    def test_free_key_gets_403(self):
        client, key = _make_client(LicenseTier.FREE)
        resp = client.post(
            "/v1/cost/analyze",
            json=self._payload(),
            headers={"X-TokenPak-Key": key},
        )
        assert resp.status_code == 403
        assert "Pro+" in resp.json().get("detail", "")

    def test_no_key_gets_401(self):
        client, _ = _make_client()
        resp = client.post("/v1/cost/analyze", json=self._payload())
        assert resp.status_code == 401

    def test_enterprise_key_succeeds(self):
        client, key = _make_client(LicenseTier.ENTERPRISE)
        resp = client.post(
            "/v1/cost/analyze",
            json=self._payload(n=14),
            headers={"X-TokenPak-Key": key},
        )
        assert resp.status_code == 200

    def test_response_includes_budget_alert(self):
        client, key = _make_client(LicenseTier.PRO)
        payload = self._payload(n=30, cost=2.0)  # 60 USD / month
        payload["monthly_budget_usd"] = 50.0
        resp = client.post(
            "/v1/cost/analyze",
            json=payload,
            headers={"X-TokenPak-Key": key},
        )
        assert resp.status_code == 200
        data = resp.json()
        alert = data["budget_alert"]
        assert alert["level"] in ("warn", "critical")

    def test_empty_metrics_rejected(self):
        client, key = _make_client()
        resp = client.post(
            "/v1/cost/analyze",
            json={"metrics": []},
            headers={"X-TokenPak-Key": key},
        )
        assert resp.status_code == 422  # validation error


class TestCostProjectionsEndpoint:
    def test_basic_projections(self):
        client, key = _make_client(LicenseTier.PRO)
        resp = client.get(
            "/v1/cost/projections",
            params={"daily_costs": "1.0,1.1,1.0,0.9,1.2,1.0,1.1"},
            headers={"X-TokenPak-Key": key},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "7d" in data["projections"]
        assert "30d" in data["projections"]
        assert data["projections"]["7d"]["projected_cost_usd"] >= 0

    def test_free_tier_blocked(self):
        client, key = _make_client(LicenseTier.FREE)
        resp = client.get(
            "/v1/cost/projections",
            params={"daily_costs": "1.0,2.0,1.5"},
            headers={"X-TokenPak-Key": key},
        )
        assert resp.status_code == 403

    def test_invalid_costs_rejected(self):
        client, key = _make_client()
        resp = client.get(
            "/v1/cost/projections",
            params={"daily_costs": "abc,xyz"},
            headers={"X-TokenPak-Key": key},
        )
        assert resp.status_code == 400

    def test_budget_alert_included_when_provided(self):
        client, key = _make_client(LicenseTier.PRO)
        resp = client.get(
            "/v1/cost/projections",
            params={"daily_costs": "5.0,5.0,5.0,5.0,5.0", "daily_budget": "1.0"},
            headers={"X-TokenPak-Key": key},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["budget_alert"] is not None
        assert data["budget_alert"]["level"] in ("warn", "critical")


class TestCostRecommendationsEndpoint:
    def test_opus_recommendations(self):
        client, key = _make_client(LicenseTier.PRO)
        resp = client.get(
            "/v1/cost/recommendations",
            params={"model": "claude-opus-4-5", "monthly_cost_usd": "200.0"},
            headers={"X-TokenPak-Key": key},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["model"] == "claude-opus-4-5"
        assert len(data["recommendations"]) >= 1
        for rec in data["recommendations"]:
            assert rec["estimated_savings_pct"] >= 20

    def test_cheap_model_no_recommendations(self):
        client, key = _make_client(LicenseTier.PRO)
        resp = client.get(
            "/v1/cost/recommendations",
            params={"model": "gemini-2-flash", "monthly_cost_usd": "5.0"},
            headers={"X-TokenPak-Key": key},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["recommendations"] == []

    def test_free_tier_blocked(self):
        client, key = _make_client(LicenseTier.FREE)
        resp = client.get(
            "/v1/cost/recommendations",
            params={"model": "gpt-4o", "monthly_cost_usd": "100.0"},
            headers={"X-TokenPak-Key": key},
        )
        assert resp.status_code == 403

    def test_team_tier_allowed(self):
        client, key = _make_client(LicenseTier.TEAM)
        resp = client.get(
            "/v1/cost/recommendations",
            params={"model": "gpt-4o", "monthly_cost_usd": "50.0"},
            headers={"X-TokenPak-Key": key},
        )
        assert resp.status_code == 200
