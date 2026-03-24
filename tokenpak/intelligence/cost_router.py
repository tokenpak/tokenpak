"""Cost Intelligence API endpoints — Pro+ feature.

Routes
──────
POST /v1/cost/analyze          — full analysis (trends + anomalies + projections + recommendations)
GET  /v1/cost/projections      — 7d/30d cost projections (query params)
GET  /v1/cost/recommendations  — model-switch recommendations (query params)

All routes require Pro+ license (tier ≠ free).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .auth import LicenseTier
from .cost_intelligence import CostIntelligence, DailyMetric

cost_router = APIRouter(tags=["cost-intelligence"])

# ---------------------------------------------------------------------------
# Pro+ guard helper
# ---------------------------------------------------------------------------

_PRO_TIERS = {LicenseTier.PRO, LicenseTier.TEAM, LicenseTier.ENTERPRISE}


def _require_pro(request: Request) -> Optional[JSONResponse]:
    """Return a 403 JSONResponse if the caller is on the free tier."""
    tier: LicenseTier = getattr(request.state, "tier", LicenseTier.FREE)
    if tier not in _PRO_TIERS:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={
                "error": "Forbidden",
                "detail": (
                    "Cost Intelligence is a Pro+ feature. " "Upgrade at https://tokenpak.ai/pricing"
                ),
            },
        )
    return None


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class MetricRecord(BaseModel):
    """One day of aggregated cost/usage data submitted by the client."""

    date_utc: str = Field(..., description="ISO date (YYYY-MM-DD)")
    cost_usd: float = Field(0.0, ge=0.0, description="Total cost for the day in USD")
    input_tokens: int = Field(0, ge=0)
    output_tokens: int = Field(0, ge=0)
    tokens_saved: int = Field(0, ge=0, description="Tokens saved by compression")
    requests: int = Field(0, ge=0)
    model: str = Field("", max_length=128, description="Primary model used")


class AnalyzeRequest(BaseModel):
    """Request body for POST /v1/cost/analyze."""

    metrics: List[MetricRecord] = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Anonymized daily metrics (up to 500 days)",
    )
    monthly_budget_usd: Optional[float] = Field(
        None,
        ge=0.0,
        description="Optional monthly budget cap in USD for alert evaluation",
    )
    anomaly_threshold: float = Field(
        2.0,
        ge=1.1,
        le=10.0,
        description="Spike detection threshold (default 2× baseline)",
    )


# ---------------------------------------------------------------------------
# POST /v1/cost/analyze
# ---------------------------------------------------------------------------


@cost_router.post(
    "/cost/analyze",
    summary="Full cost intelligence analysis (Pro+)",
    description=(
        "Submit anonymized daily usage metrics and receive trends, "
        "per-model breakdown, anomaly detection, projections, model "
        "recommendations, and budget alerts. Requires Pro+ license."
    ),
)
async def cost_analyze(body: AnalyzeRequest, request: Request) -> Dict[str, Any]:
    guard = _require_pro(request)
    if guard:
        return guard  # type: ignore[return-value]

    request_id: str = request.state.request_id

    daily_metrics = [
        DailyMetric(
            date_utc=m.date_utc,
            cost_usd=m.cost_usd,
            input_tokens=m.input_tokens,
            output_tokens=m.output_tokens,
            tokens_saved=m.tokens_saved,
            requests=m.requests,
            model=m.model,
        )
        for m in body.metrics
    ]

    result = CostIntelligence.analyze(
        daily_metrics,
        monthly_budget_usd=body.monthly_budget_usd,
        anomaly_threshold=body.anomaly_threshold,
    )
    result["request_id"] = request_id
    return result


# ---------------------------------------------------------------------------
# GET /v1/cost/projections
# ---------------------------------------------------------------------------


@cost_router.get(
    "/cost/projections",
    summary="7d/30d cost projections (Pro+)",
    description=(
        "Compute forward cost projections from historical daily-cost data. "
        "Pass comma-separated daily costs via `daily_costs` query param. "
        "Requires Pro+ license."
    ),
)
async def cost_projections(
    request: Request,
    daily_costs: str = Query(
        ...,
        description="Comma-separated daily costs in USD (newest last), e.g. '1.5,2.1,1.8'",
    ),
    daily_budget: Optional[float] = Query(
        None,
        ge=0.0,
        description="Optional daily budget for alert (monthly = daily × 30)",
    ),
) -> Dict[str, Any]:
    guard = _require_pro(request)
    if guard:
        return guard  # type: ignore[return-value]

    request_id: str = request.state.request_id

    try:
        cost_values = [float(v.strip()) for v in daily_costs.split(",") if v.strip()]
    except ValueError:
        return JSONResponse(  # type: ignore[return-value]
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "Invalid daily_costs — must be comma-separated floats"},
        )

    if not cost_values:
        return JSONResponse(  # type: ignore[return-value]
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "daily_costs must contain at least one value"},
        )

    from datetime import date, timedelta

    today = date.today()
    metrics = [
        DailyMetric(
            date_utc=(today - timedelta(days=len(cost_values) - 1 - i)).isoformat(),
            cost_usd=max(0.0, v),
            input_tokens=0,
            output_tokens=0,
        )
        for i, v in enumerate(cost_values)
    ]

    projections = CostIntelligence.compute_projections(metrics)

    # Budget alert on 30d projection if daily_budget supplied
    alert = None
    if daily_budget is not None and daily_budget > 0:
        monthly_budget = daily_budget * 30
        spent = projections["30d"].projected_cost_usd
        alert_obj = CostIntelligence.check_budget_alert(spent, monthly_budget)
        from dataclasses import asdict

        alert = asdict(alert_obj)

    from dataclasses import asdict as _asdict

    return {
        "projections": {k: _asdict(v) for k, v in projections.items()},
        "budget_alert": alert,
        "request_id": request_id,
    }


# ---------------------------------------------------------------------------
# GET /v1/cost/recommendations
# ---------------------------------------------------------------------------


@cost_router.get(
    "/cost/recommendations",
    summary="Model-switch recommendations (Pro+)",
    description=(
        "Get recommendations for cheaper model alternatives based on current usage. "
        "Pass `model` and `monthly_cost_usd` as query params. "
        "Requires Pro+ license."
    ),
)
async def cost_recommendations(
    request: Request,
    model: str = Query(..., max_length=128, description="Current primary model"),
    monthly_cost_usd: float = Query(
        ..., ge=0.0, description="Current monthly cost for this model in USD"
    ),
    monthly_budget_usd: Optional[float] = Query(
        None, ge=0.0, description="Optional monthly budget cap"
    ),
) -> Dict[str, Any]:
    guard = _require_pro(request)
    if guard:
        return guard  # type: ignore[return-value]

    request_id: str = request.state.request_id

    # Synthesize a minimal model_breakdown entry
    model_breakdown = [
        {
            "model": model,
            "cost_usd": monthly_cost_usd,
            "input_tokens": 0,
            "output_tokens": 0,
            "tokens_saved": 0,
            "requests": 0,
            "days": 30,  # treat as a full month
        }
    ]

    recs = CostIntelligence.compute_recommendations(
        model_breakdown, monthly_budget_usd=monthly_budget_usd
    )

    from dataclasses import asdict

    return {
        "model": model,
        "monthly_cost_usd": monthly_cost_usd,
        "recommendations": [asdict(r) for r in recs],
        "request_id": request_id,
    }
