"""
Account-scoped Dashboard — Per-license usage, savings, and ROI views.

Routes:
  GET /dashboard/account/usage — Token usage over time (personal)
  GET /dashboard/account/savings — Compression savings breakdown
  GET /dashboard/account/roi — ROI calculator (savings in dollars)

Requires:
  - WS-1: Feature gates (Pro+ only)
  - WS-5: Usage metering (keyed by license key_id)
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# License & Tier Detection
# ─────────────────────────────────────────────


def _get_license_key_id() -> Optional[str]:
    """Extract license key_id from environment or license file."""
    # Priority:
    # 1. TOKENPAK_LICENSE_KEY env var
    # 2. ~/.tokenpak/license.key (parsed)
    # 3. None (OSS / unlicensed)

    env_key = os.environ.get("TOKENPAK_LICENSE_KEY", "").strip()
    if env_key:
        # Extract key_id from TPAK-... format or return as-is
        return env_key

    license_file = Path.home() / ".tokenpak" / "license.key"
    if license_file.exists():
        try:
            from .activation import get_license_key
            key = get_license_key()
            return key  # Already parsed as key_id
        except Exception as e:
            logger.warning(f"Failed to load license key: {e}")

    return None


def _check_pro_access(request: Request) -> Optional[str]:
    """
    Check if user has Pro+ access. Return key_id if yes, raise 403 if no.

    Uses:
      - License tier detection
      - WS-1 feature gates (if available)
    """
    key_id = _get_license_key_id()

    if not key_id:
        # OSS user — no access to account dashboard
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": "Account dashboard requires TokenPak Pro or higher",
                "upgrade_url": "https://tokenpak.io/pricing",
                "upgrade_cta": "Upgrade to Pro to unlock personal analytics",
            }
        )

    # TODO: Check tier from license payload
    # For now, assume any licensed user is Pro+
    # Future: verify tier == "pro" | "team" | "enterprise"

    return key_id


# ─────────────────────────────────────────────
# Data Loaders (from metering.db)
# ─────────────────────────────────────────────


def _load_usage_data(key_id: str, start_date: str, end_date: str) -> list[dict]:
    """
    Load usage summary for given key_id and date range.

    Returns:
      [
        {
          "date": "2026-03-22",
          "model": "claude-sonnet",
          "input_tokens": 50000,
          "output_tokens": 10000,
          "saved_tokens": 5000,
          "request_count": 42
        },
        ...
      ]
    """
    try:
        from tokenpak.metering import UsageMeterManager

        manager = UsageMeterManager()
        meter = manager.get_meter(key_id)

        # Fetch daily summaries for the range
        start = datetime.fromisoformat(start_date)
        end = datetime.fromisoformat(end_date)
        current = start

        results = []
        while current <= end:
            date_str = current.strftime("%Y-%m-%d")
            summary = meter.get_daily_summary(date_str)
            if summary and summary.get("total_input", 0) > 0:
                results.append({
                    "date": date_str,
                    "model": "all",  # TODO: break down by model
                    "input_tokens": summary.get("total_input", 0),
                    "output_tokens": summary.get("total_output", 0),
                    "saved_tokens": summary.get("total_saved", 0),
                    "request_count": summary.get("request_count", 0),
                })
            current += timedelta(days=1)

        return results
    except Exception as e:
        logger.warning(f"Failed to load usage data: {e}")
        return []


def _calculate_roi(saved_tokens: int) -> dict:
    """
    Calculate dollar savings from saved tokens.

    Uses per-model pricing (from tokenpak.pricing or hard-coded fallback).

    Returns:
      {
        "total_saved_tokens": 5000,
        "estimated_savings_usd": 0.15,
        "breakdown": [
          {"model": "claude-sonnet", "tokens_saved": 3000, "savings_usd": 0.09},
          {"model": "claude-opus", "tokens_saved": 2000, "savings_usd": 0.06},
        ]
      }
    """
    # Fallback model pricing (input tokens per $1M)
    MODEL_PRICING = {
        "claude-opus": 15000,  # $0.067/1K input
        "claude-sonnet": 50000,  # $0.003/1K input
        "claude-haiku": 800000,  # $0.00125/1K input
    }

    # Average savings assuming 50% Sonnet, 40% Haiku, 10% Opus
    avg_cost_per_token = (0.5 * (1_000_000 / 50000) +
                          0.4 * (1_000_000 / 800000) +
                          0.1 * (1_000_000 / 15000)) / 1_000_000

    estimated_savings = saved_tokens * avg_cost_per_token

    return {
        "total_saved_tokens": saved_tokens,
        "estimated_savings_usd": round(estimated_savings, 4),
        "period": "since activation",
    }


# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────

router = APIRouter(prefix="/dashboard/account", tags=["account-dashboard"])

_TEMPLATES_DIR = Path(__file__).parent / "templates" / "account"
_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
templates.env.globals.update({"max": max, "min": min, "abs": abs, "round": round})


@router.get("/usage", response_class=HTMLResponse)
def account_usage(request: Request,
                  days: int = 7,
                  model: Optional[str] = None):
    """
    Personal token usage over time.

    Query params:
      ?days=7 — last N days (default: 7)
      ?model=claude-sonnet — filter by model (optional)
    """
    key_id = _check_pro_access(request)

    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)

    data = _load_usage_data(
        key_id,
        start_date.isoformat(),
        end_date.isoformat()
    )

    # Aggregate by day for chart
    daily_totals = {}
    for row in data:
        day = row["date"]
        if day not in daily_totals:
            daily_totals[day] = {"input": 0, "output": 0, "saved": 0}
        daily_totals[day]["input"] += row["input_tokens"]
        daily_totals[day]["output"] += row["output_tokens"]
        daily_totals[day]["saved"] += row["saved_tokens"]

    chart_labels = sorted(daily_totals.keys())
    chart_input = [daily_totals[d]["input"] for d in chart_labels]
    chart_output = [daily_totals[d]["output"] for d in chart_labels]
    chart_saved = [daily_totals[d]["saved"] for d in chart_labels]

    # Summary stats
    total_input = sum(daily_totals[d]["input"] for d in daily_totals)
    total_output = sum(daily_totals[d]["output"] for d in daily_totals)
    total_saved = sum(daily_totals[d]["saved"] for d in daily_totals)

    return templates.TemplateResponse(request, "usage.html", {
        "key_id": key_id,
        "days": days,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "total_input": total_input,
        "total_output": total_output,
        "total_saved": total_saved,
        "chart_labels": chart_labels,
        "chart_input": chart_input,
        "chart_output": chart_output,
        "chart_saved": chart_saved,
    })


@router.get("/savings", response_class=HTMLResponse)
def account_savings(request: Request,
                    days: int = 30,
                    breakdown: str = "daily"):
    """
    Compression savings breakdown — tokens saved, compression ratio trends.

    Query params:
      ?days=30 — lookback period (default: 30)
      ?breakdown=daily|weekly|monthly — aggregation (default: daily)
    """
    key_id = _check_pro_access(request)

    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)

    data = _load_usage_data(
        key_id,
        start_date.isoformat(),
        end_date.isoformat()
    )

    # Aggregate by breakdown period
    period_totals = {}
    for row in data:
        date_obj = datetime.fromisoformat(row["date"]).date()

        if breakdown == "weekly":
            period_key = date_obj.isocalendar()[0:2]  # (year, week)
        elif breakdown == "monthly":
            period_key = (date_obj.year, date_obj.month)
        else:  # daily
            period_key = row["date"]

        if period_key not in period_totals:
            period_totals[period_key] = {
                "input": 0,
                "output": 0,
                "saved": 0,
                "total": 0,
                "requests": 0,
            }

        period_totals[period_key]["input"] += row["input_tokens"]
        period_totals[period_key]["output"] += row["output_tokens"]
        period_totals[period_key]["saved"] += row["saved_tokens"]
        period_totals[period_key]["total"] += (row["input_tokens"] +
                                                row["output_tokens"])
        period_totals[period_key]["requests"] += row.get("request_count", 0)

    # Calculate compression ratios
    savings_data = []
    cumulative_saved = 0
    for period_key in sorted(period_totals.keys()):
        totals = period_totals[period_key]
        if totals["total"] == 0:
            ratio = 0
        else:
            ratio = totals["saved"] / totals["total"]

        cumulative_saved += totals["saved"]

        period_label = (
            f"{period_key}" if isinstance(period_key, str)
            else f"W{period_key[1]}" if breakdown == "weekly"
            else f"{period_key[0]}-{period_key[1]:02d}"
        )

        savings_data.append({
            "period": period_label,
            "tokens_saved": totals["saved"],
            "compression_ratio": round(ratio * 100, 2),
            "cumulative_saved": cumulative_saved,
            "requests": totals["requests"],
        })

    return templates.TemplateResponse(request, "savings.html", {
        "key_id": key_id,
        "days": days,
        "breakdown": breakdown,
        "savings_data": savings_data,
        "total_saved": cumulative_saved,
        "avg_compression": (
            round(sum(s["compression_ratio"] for s in savings_data) /
                  max(len(savings_data), 1), 2) if savings_data else 0
        ),
    })


@router.get("/roi", response_class=HTMLResponse)
def account_roi(request: Request):
    """
    ROI calculator — shows estimated dollar savings from tokens saved.
    """
    key_id = _check_pro_access(request)

    # Get all-time usage
    thirty_days_ago = (date.today() - timedelta(days=30)).isoformat()
    today = date.today().isoformat()

    data = _load_usage_data(key_id, thirty_days_ago, today)
    total_saved = sum(row["saved_tokens"] for row in data)

    roi = _calculate_roi(total_saved)

    return templates.TemplateResponse(request, "roi.html", {
        "key_id": key_id,
        "total_saved": total_saved,
        "estimated_savings_usd": roi["estimated_savings_usd"],
        "period": roi["period"],
    })


# ─────────────────────────────────────────────
# JSON API Endpoints (for AJAX)
# ─────────────────────────────────────────────


@router.get("/api/usage.json")
def api_usage(request: Request, days: int = 7) -> JSONResponse:
    """Return usage data as JSON (for charting libraries)."""
    key_id = _check_pro_access(request)

    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)

    data = _load_usage_data(
        key_id,
        start_date.isoformat(),
        end_date.isoformat()
    )

    return JSONResponse({
        "key_id": key_id,
        "period": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "data": data,
    })


@router.get("/api/savings.json")
def api_savings(request: Request, days: int = 30) -> JSONResponse:
    """Return savings data as JSON."""
    key_id = _check_pro_access(request)

    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)

    data = _load_usage_data(
        key_id,
        start_date.isoformat(),
        end_date.isoformat()
    )

    total_saved = sum(row["saved_tokens"] for row in data)
    roi = _calculate_roi(total_saved)

    return JSONResponse({
        "key_id": key_id,
        "period": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "total_saved_tokens": total_saved,
        "estimated_savings_usd": roi["estimated_savings_usd"],
    })
