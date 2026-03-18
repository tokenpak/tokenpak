"""tokenpak/agent/dashboard/app.py — Phase 5C Dashboard UI"""
from __future__ import annotations
import json, logging, os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from tokenpak.agent.query.api import EntryStore
from tokenpak.agent.query.timeline import TimelineGenerator
from tokenpak.agent.query.audit import AuditGenerator

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
_store = EntryStore()
_timeline = TimelineGenerator()
_audit = AuditGenerator()

def _today() -> str:
    return date.today().strftime("%Y-%m-%d")

def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).strftime("%Y-%m-%d")

def _overview_data(query_date: str) -> dict:
    stats = _store.compute_stats(query_date)
    top_users = _store.top_users(query_date, limit=10)
    summary = _store.usage_summary(query_date)
    trend_start = (date.fromisoformat(query_date) - timedelta(days=6)).strftime("%Y-%m-%d")
    cache_trends = _store.cache_trends(trend_start, query_date)
    return {"stats": stats, "top_users": top_users, "summary": summary, "cache_trends": cache_trends}

router = APIRouter(tags=["dashboard"])

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_overview(request: Request, date_str: Optional[str] = Query(None, alias="date")):
    """Main overview dashboard."""
    q = date_str or _today()
    data = _overview_data(q)
    return templates.TemplateResponse(request, "overview.html",
        {"query_date": q, "today": _today(), "yesterday": _days_ago(1), **data})

@router.get("/dashboard/time-series", response_class=HTMLResponse)
def dashboard_time_series(request: Request, start: Optional[str] = Query(None),
    end: Optional[str] = Query(None), window: int = Query(5, ge=1, le=1440)):
    """Time-series graphs page."""
    end_date = end or _today()
    start_date = start or _days_ago(6)
    rollups = _store.compute_rollups(start_date, end_date, window_minutes=window)
    return templates.TemplateResponse(request, "time_series.html", {
        "start_date": start_date, "end_date": end_date,
        "window": window, "rollup_count": len(rollups),
        "chart_labels": json.dumps([r["timestamp"] for r in rollups]),
        "tokens_data": json.dumps([r["total_tokens"] for r in rollups]),
        "cache_data": json.dumps([r["cache_tokens"] for r in rollups]),
        "requests_data": json.dumps([r["request_count"] for r in rollups]),
    })

@router.get("/dashboard/agents", response_class=HTMLResponse)
def dashboard_agents(request: Request, date_str: Optional[str] = Query(None, alias="date"),
    sort: str = Query("requests")):
    """Agent leaderboard page."""
    q = date_str or _today()
    top_users = _store.top_users(q, limit=50)
    comp_map = {c["agent_id"]: c for c in _store.compression_ratios(q)}
    rows = []
    for u in top_users:
        aid = u["agent_id"]
        c = comp_map.get(aid, {})
        rows.append({"agent_id": aid, "request_count": u["request_count"],
            "total_tokens": u["total_tokens"],
            "avg_compression_ratio": c.get("avg_compression_ratio", 0.0),
            "sample_count": c.get("sample_count", 0)})
    sort_keys = {"requests": lambda x: x["request_count"],
                 "tokens": lambda x: x["total_tokens"],
                 "compression": lambda x: x["avg_compression_ratio"]}
    rows.sort(key=sort_keys.get(sort, sort_keys["requests"]), reverse=True)
    return templates.TemplateResponse(request, "agents.html",
        {"query_date": q, "today": _today(), "sort": sort, "rows": rows})

@router.get("/dashboard/htmx/stats", response_class=HTMLResponse)
def htmx_stats(request: Request, date_str: Optional[str] = Query(None, alias="date")):
    q = date_str or _today()
    return templates.TemplateResponse(request, "partials/stat_cards.html",
        {"stats": _store.compute_stats(q), "summary": _store.usage_summary(q), "query_date": q})

@router.get("/dashboard/htmx/top-users", response_class=HTMLResponse)
def htmx_top_users(request: Request, date_str: Optional[str] = Query(None, alias="date")):
    q = date_str or _today()
    return templates.TemplateResponse(request, "partials/top_users.html",
        {"top_users": _store.top_users(q, limit=10), "query_date": q})

@router.get("/dashboard/timeline", response_class=HTMLResponse)
def dashboard_timeline(request: Request, hours: int = Query(24, ge=1, le=168)):
    """Timeline dashboard — hourly cost visualization for the last N hours."""
    # Load entries from last N hours
    start_date = _days_ago(hours // 24 + 1)
    end_date = _today()
    entries = _store.read_entries(start_date, end_date)
    
    # Generate hourly buckets
    now = datetime.now(tz=timezone.utc).replace(minute=0, second=0, microsecond=0)
    buckets = _timeline.hourly_buckets(entries, start_hour=now, num_hours=hours)
    
    # Extract chart data
    chart_labels = json.dumps([b["hour"] for b in buckets])
    chart_costs = json.dumps([round(b["cost"], 4) for b in buckets])
    chart_requests = json.dumps([b["requests"] for b in buckets])
    
    return templates.TemplateResponse(request, "timeline.html", {
        "hours": hours,
        "bucket_count": len(buckets),
        "total_cost": sum(b["cost"] for b in buckets),
        "total_requests": sum(b["requests"] for b in buckets),
        "chart_labels": chart_labels,
        "chart_costs": chart_costs,
        "chart_requests": chart_requests,
    })

@router.get("/dashboard/api/timeline-data", response_class=JSONResponse)
def api_timeline_data(
    hours: int = Query(24, ge=1, le=168),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    """JSON endpoint for timeline data."""
    if not start_date or not end_date:
        end_date = _today()
        start_date = _days_ago(hours // 24 + 1)
    
    entries = _store.read_entries(start_date, end_date)
    now = datetime.now(tz=timezone.utc).replace(minute=0, second=0, microsecond=0)
    buckets = _timeline.hourly_buckets(entries, start_hour=now, num_hours=hours)
    
    return {
        "hours": hours,
        "buckets": buckets,
        "summary": {
            "total_cost": sum(b["cost"] for b in buckets),
            "total_requests": sum(b["requests"] for b in buckets),
            "avg_request_cost": sum(b["cost"] for b in buckets) / max(sum(b["requests"] for b in buckets), 1),
        }
    }

@router.get("/dashboard/audit", response_class=HTMLResponse)
def dashboard_audit(request: Request, date_str: Optional[str] = Query(None, alias="date")):
    """Audit dashboard — cost breakdown by model and feature."""
    q = date_str or _today()
    entries = _store.read_entries(q, q)
    audit = _audit.session_audit(entries)
    
    # Prepare chart data
    models_labels = json.dumps(list(audit["models"].keys()))
    models_costs = json.dumps([audit["models"][m]["cost"] for m in audit["models"].keys()])
    models_pcts = json.dumps([audit["models"][m]["percentage"] for m in audit["models"].keys()])
    
    features_labels = json.dumps(list(audit["features"].keys()))
    features_costs = json.dumps([audit["features"][f]["cost"] for f in audit["features"].keys()])
    features_pcts = json.dumps([audit["features"][f]["percentage"] for f in audit["features"].keys()])
    
    return templates.TemplateResponse(request, "audit.html", {
        "query_date": q,
        "today": _today(),
        "total_spend": audit["total_spend"],
        "request_count": audit["request_count"],
        "avg_cost": audit["avg_cost_per_request"],
        "models": audit["models"],
        "features": audit["features"],
        "models_labels": models_labels,
        "models_costs": models_costs,
        "models_pcts": models_pcts,
        "features_labels": features_labels,
        "features_costs": features_costs,
        "features_pcts": features_pcts,
    })

@router.get("/dashboard/api/audit", response_class=JSONResponse)
def api_audit(
    date_str: Optional[str] = Query(None, alias="date"),
):
    """JSON endpoint for audit data."""
    q = date_str or _today()
    entries = _store.read_entries(q, q)
    audit = _audit.session_audit(entries)
    audit["generated_at"] = datetime.now(tz=timezone.utc).isoformat()
    return audit

def create_dashboard_app() -> FastAPI:
    app = FastAPI(title="TokenPak Dashboard", version="5.0.0")
    app.include_router(router)
    @app.get("/health")
    def health(): return {"status": "ok", "service": "tokenpak-dashboard"}
    return app

def create_combined_app() -> FastAPI:
    """Ingest + Query + Dashboard on port 17888."""
    from tokenpak.agent.ingest.api import router as ingest_router
    from tokenpak.agent.query.api import router as query_router
    app = FastAPI(title="TokenPak API + Dashboard", version="5.3.0")
    app.include_router(ingest_router)
    app.include_router(query_router)
    app.include_router(router)
    @app.get("/health")
    def health(): return {"status": "ok", "service": "tokenpak-combined", "version": "5.3.0"}
    return app
