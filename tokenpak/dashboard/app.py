"""tokenpak/agent/dashboard/app.py — Phase 5C Dashboard UI"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from tokenpak.telemetry.query.api import EntryStore
from tokenpak.telemetry.query.audit import AuditGenerator
from tokenpak.telemetry.query.timeline import TimelineGenerator

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
# Expose Python builtins needed by templates (e.g., max/min in timeline.html)
templates.env.globals.update({"max": max, "min": min, "abs": abs, "round": round})
_store = EntryStore()
_timeline = TimelineGenerator()
_audit = AuditGenerator()

# Valid consumption modes (CCI-09)
VALID_MODES = frozenset({"cli", "tui", "tmux", "sdk", "ide", "cron"})


def _today() -> str:
    return date.today().strftime("%Y-%m-%d")


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).strftime("%Y-%m-%d")


def _detect_active_mode() -> str:
    """Infer consumption mode from environment (CCI-04 active profile)."""
    if os.environ.get("TOKENPAK_MODE"):
        m = os.environ["TOKENPAK_MODE"].lower()
        if m in VALID_MODES:
            return m
    if os.environ.get("TMUX") or os.environ.get("TMUX_PANE"):
        return "tmux"
    if os.environ.get("TERM_PROGRAM", "").lower() in ("iterm.app", "ghostty"):
        return "tui"
    if os.environ.get("VSCODE_PID") or os.environ.get("JETBRAINS_IDE"):
        return "ide"
    if os.environ.get("TOKENPAK_JOB_NAME") or os.environ.get("CRON_JOB"):
        return "cron"
    return "cli"


def _mode_data(mode: str, date_str: str) -> dict[str, Any]:
    """Build mode-specific context dict for the per_mode template."""
    stats = _store.compute_stats(date_str)
    summary = _store.usage_summary(date_str)
    entries = _store.read_entries(date_str, date_str)

    header = {
        "total_cost": stats.get("total_cost", 0.0),
        "cache_hit_pct": stats.get("cache_hit_pct", 0.0),
        "request_count": stats.get("request_count", 0),
    }

    ctx: dict[str, Any] = {
        "active_mode": mode,
        "active_profile": os.environ.get("TOKENPAK_COMPANION_PROFILE", "balanced"),
        "header": header,
        "stats": stats,
        "query_date": date_str,
    }

    if mode == "cli":
        ctx.update(_cli_data(entries))
    elif mode == "tui":
        ctx.update(_tui_data(entries))
    elif mode == "tmux":
        ctx.update(_tmux_data(entries, stats))
    elif mode == "sdk":
        ctx.update(_sdk_data(entries))
    elif mode == "ide":
        ctx.update(_ide_data())
    elif mode == "cron":
        ctx.update(_cron_data())

    return ctx


def _cli_data(entries: list[dict]) -> dict[str, Any]:
    # cost_by_repo: group by extra.working_dir or extra.repo_hash if present
    repo_map: dict[str, dict] = {}
    for e in entries:
        repo = ((e.get("extra") or {}).get("working_dir") or
                (e.get("extra") or {}).get("repo_hash") or "")
        if not repo:
            continue
        if repo not in repo_map:
            repo_map[repo] = {"repo": repo, "request_count": 0, "cost": 0.0}
        repo_map[repo]["request_count"] += 1
        repo_map[repo]["cost"] += e.get("cost", 0.0)
    cost_by_repo = sorted(repo_map.values(), key=lambda x: x["cost"], reverse=True)[:10]
    return {
        "cost_by_repo": cost_by_repo,
        "burn_rate": None,
        "doctor_runs": [],
    }


def _tui_data(entries: list[dict]) -> dict[str, Any]:
    # Build savings tape from last 10 sessions with non-null session_id
    seen: dict[str, dict] = {}
    for e in entries:
        sid = e.get("session_id") or ""
        if not sid:
            continue
        if sid not in seen:
            seen[sid] = {"session_id": sid, "tokens": 0, "cost": 0.0, "saved_pct": 0.0}
        seen[sid]["tokens"] += e.get("tokens", 0)
        seen[sid]["cost"] += e.get("cost", 0.0)
        comp = (e.get("extra") or {}).get("compression_pct", 0.0)
        seen[sid]["saved_pct"] = max(seen[sid]["saved_pct"], comp)

    tape = sorted(seen.values(), key=lambda x: x["cost"], reverse=True)[:10]
    total_cost = sum(e.get("cost", 0.0) for e in entries)
    total_tokens = sum(e.get("tokens", 0) for e in entries)
    return {
        "session_cost": total_cost,
        "session_tokens": total_tokens,
        "savings_pct": None,
        "savings_tape": tape,
    }


def _tmux_data(entries: list[dict], stats: dict) -> dict[str, Any]:
    agent_map: dict[str, dict] = {}
    for e in entries:
        aid = e.get("agent") or "unknown"
        if aid not in agent_map:
            agent_map[aid] = {"agent_id": aid, "request_count": 0, "total_tokens": 0, "cost": 0.0}
        agent_map[aid]["request_count"] += 1
        agent_map[aid]["total_tokens"] += e.get("tokens", 0)
        agent_map[aid]["cost"] += e.get("cost", 0.0)

    total_cost = stats.get("total_cost", 0.0) or 1e-9
    agent_rows = sorted(agent_map.values(), key=lambda x: x["cost"], reverse=True)[:20]
    for row in agent_rows:
        row["budget_pct"] = (row["cost"] / total_cost) * 100

    # Fairness: any single agent using >70% of budget is "skewed"
    fairness_ok = all(r["budget_pct"] <= 70 for r in agent_rows)
    return {
        "agent_rows": agent_rows,
        "concurrent_sessions": len(agent_map),
        "fairness_ok": fairness_ok,
    }


def _sdk_data(entries: list[dict]) -> dict[str, Any]:
    model_map: dict[str, dict] = {}
    for e in entries:
        m = e.get("model") or "unknown"
        if m not in model_map:
            model_map[m] = {"model": m, "request_count": 0, "total_tokens": 0, "cost": 0.0}
        model_map[m]["request_count"] += 1
        model_map[m]["total_tokens"] += e.get("tokens", 0)
        model_map[m]["cost"] += e.get("cost", 0.0)

    otlp_endpoint = os.environ.get("TOKENPAK_OTLP_ENDPOINT", "")
    otlp_status = "active" if otlp_endpoint else "not configured"

    return {
        "model_usage": sorted(model_map.values(), key=lambda x: x["request_count"], reverse=True),
        "error_count": 0,
        "otlp_status": otlp_status,
    }


def _ide_data() -> dict[str, Any]:
    workspace = (os.environ.get("VSCODE_WORKSPACE_FOLDER") or
                 os.environ.get("JETBRAINS_PROJECT") or "")
    return {
        "active_workspace": workspace or None,
        "inline_savings_tokens": None,
        "inline_savings_cost": None,
        "timeout_count": 0,
        "timeout_events": [],
    }


def _cron_data() -> dict[str, Any]:
    return {
        "job_stats": [],
        "success_rate": None,
        "telegram_alerts": None,
        "budget_enforcements": None,
    }


def _overview_data(query_date: str) -> dict:
    stats = _store.compute_stats(query_date)
    top_users = _store.top_users(query_date, limit=10)
    summary = _store.usage_summary(query_date)
    trend_start = (date.fromisoformat(query_date) - timedelta(days=6)).strftime("%Y-%m-%d")
    cache_trends = _store.cache_trends(trend_start, query_date)
    return {
        "stats": stats,
        "top_users": top_users,
        "summary": summary,
        "cache_trends": cache_trends,
    }


router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_overview(
    request: Request,
    date_str: Optional[str] = Query(None, alias="date"),
    mode: Optional[str] = Query(None),
):
    """Main dashboard — overview or per-mode view when ?mode= is provided."""
    q = date_str or _today()
    if mode is not None:
        if mode not in VALID_MODES:
            raise HTTPException(status_code=404, detail=f"Unknown mode: {mode!r}")
        ctx = _mode_data(mode, q)
        return templates.TemplateResponse(request, "per_mode.html", ctx)
    # Default: detect mode from environment (CCI-04 active profile)
    detected = _detect_active_mode()
    ctx = _mode_data(detected, q)
    return templates.TemplateResponse(request, "per_mode.html", ctx)


@router.get("/dashboard/htmx/mode/tui/cost", response_class=HTMLResponse)
def htmx_tui_cost(request: Request, date_str: Optional[str] = Query(None, alias="date")):
    """HTMX partial — live cost figure for TUI mode polling (5s interval)."""
    q = date_str or _today()
    stats = _store.compute_stats(q)
    cost = stats.get("total_cost", 0.0)
    return HTMLResponse(content=f"${cost:.6f}")


@router.get("/dashboard/time-series", response_class=HTMLResponse)
def dashboard_time_series(
    request: Request,
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    window: int = Query(5, ge=1, le=1440),
):
    """Time-series graphs page."""
    end_date = end or _today()
    start_date = start or _days_ago(6)
    rollups = _store.compute_rollups(start_date, end_date, window_minutes=window)
    return templates.TemplateResponse(
        request,
        "time_series.html",
        {
            "start_date": start_date,
            "end_date": end_date,
            "window": window,
            "rollup_count": len(rollups),
            "chart_labels": json.dumps([r["timestamp"] for r in rollups]),
            "tokens_data": json.dumps([r["total_tokens"] for r in rollups]),
            "cache_data": json.dumps([r["cache_tokens"] for r in rollups]),
            "requests_data": json.dumps([r["request_count"] for r in rollups]),
        },
    )


@router.get("/dashboard/agents", response_class=HTMLResponse)
def dashboard_agents(
    request: Request,
    date_str: Optional[str] = Query(None, alias="date"),
    sort: str = Query("requests"),
):
    """Agent leaderboard page."""
    q = date_str or _today()
    top_users = _store.top_users(q, limit=50)
    comp_map = {c["agent_id"]: c for c in _store.compression_ratios(q)}
    rows = []
    for u in top_users:
        aid = u["agent_id"]
        c = comp_map.get(aid, {})
        rows.append(
            {
                "agent_id": aid,
                "request_count": u["request_count"],
                "total_tokens": u["total_tokens"],
                "avg_compression_ratio": c.get("avg_compression_ratio", 0.0),
                "sample_count": c.get("sample_count", 0),
            }
        )
    sort_keys = {
        "requests": lambda x: x["request_count"],
        "tokens": lambda x: x["total_tokens"],
        "compression": lambda x: x["avg_compression_ratio"],
    }
    rows.sort(key=sort_keys.get(sort, sort_keys["requests"]), reverse=True)
    return templates.TemplateResponse(
        request, "agents.html", {"query_date": q, "today": _today(), "sort": sort, "rows": rows}
    )


@router.get("/dashboard/htmx/stats", response_class=HTMLResponse)
def htmx_stats(request: Request, date_str: Optional[str] = Query(None, alias="date")):
    q = date_str or _today()
    return templates.TemplateResponse(
        request,
        "partials/stat_cards.html",
        {"stats": _store.compute_stats(q), "summary": _store.usage_summary(q), "query_date": q},
    )


@router.get("/dashboard/htmx/top-users", response_class=HTMLResponse)
def htmx_top_users(request: Request, date_str: Optional[str] = Query(None, alias="date")):
    q = date_str or _today()
    return templates.TemplateResponse(
        request,
        "partials/top_users.html",
        {"top_users": _store.top_users(q, limit=10), "query_date": q},
    )


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

    return templates.TemplateResponse(
        request,
        "timeline.html",
        {
            "hours": hours,
            "bucket_count": len(buckets),
            "total_cost": sum(b["cost"] for b in buckets),
            "total_requests": sum(b["requests"] for b in buckets),
            "chart_labels": chart_labels,
            "chart_costs": chart_costs,
            "chart_requests": chart_requests,
        },
    )


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
            "avg_request_cost": sum(b["cost"] for b in buckets)
            / max(sum(b["requests"] for b in buckets), 1),
        },
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
    features_pcts = json.dumps(
        [audit["features"][f]["percentage"] for f in audit["features"].keys()]
    )

    return templates.TemplateResponse(
        request,
        "audit.html",
        {
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
        },
    )


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
    def health():
        return {"status": "ok", "service": "tokenpak-dashboard"}

    return app


def create_combined_app() -> FastAPI:
    """Ingest + Query + Dashboard on port 17888."""
    from tokenpak.vault.ingest.api import router as ingest_router
    from tokenpak.telemetry.query.api import router as query_router

    app = FastAPI(title="TokenPak API + Dashboard", version="5.3.0")
    app.include_router(ingest_router)
    app.include_router(query_router)
    app.include_router(router)

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "tokenpak-combined", "version": "5.3.0"}

    return app
