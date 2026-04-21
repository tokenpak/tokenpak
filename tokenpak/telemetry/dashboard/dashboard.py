"""TokenPak Telemetry Dashboard — Phase 5C.

Provides three views over the telemetry API:
- FinOps:       Cost, savings, compression, per-model/provider breakdown
- Engineering:  Token composition, request volume, agent usage, latency
- Audit:        Trace timeline, segment breakdown, hash chain, usage reconciliation

Auto-refreshes via HTMX every 30 seconds. Static assets (Chart.js, HTMX)
are bundled and served locally.

Usage::

    from tokenpak.telemetry.dashboard.dashboard import create_dashboard_router

    router = create_dashboard_router(storage, rollups)
    app.include_router(router)
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from tokenpak.telemetry.rollups import RollupEngine
from tokenpak.telemetry.storage import TelemetryDB

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=2)

_HERE = Path(__file__).parent
_TEMPLATES_DIR = _HERE / "templates"
_STATIC_DIR = _HERE / "static"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


# ── Glossary: load term_cards.json for tooltip/glossary page ──────────────
try:
    import json as _json_mod

    _TERM_CARDS_PATH = _HERE.parent.parent / "term_cards.json"
    _GLOSSARY_DATA_GLOBAL: dict = (
        _json_mod.loads(_TERM_CARDS_PATH.read_text()) if _TERM_CARDS_PATH.exists() else {}
    )
except Exception:
    _GLOSSARY_DATA_GLOBAL = {}
templates.env.globals["glossary_data"] = _GLOSSARY_DATA_GLOBAL

# --- Notification System ---
_NOTIFICATIONS: deque = deque(maxlen=100)  # Max 100 notifications in memory
_NOTIF_MAX = 100


def _add_notification(notif_type: str, title: str, message: str) -> dict:
    """Add a notification to the queue."""
    n = {
        "id": str(uuid.uuid4()),
        "type": notif_type,
        "title": title,
        "message": message,
        "ts": int(time.time()),
        "read": False,
    }
    _NOTIFICATIONS.appendleft(n)
    return n


def _detect_cost_spike(storage) -> list:
    """Check if today's cost is >2x the average of the past 7 days."""
    try:
        db_path = storage._db_path if hasattr(storage, "_db_path") else str(storage)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        today = datetime.now().date().isoformat()
        week_ago = (datetime.now() - timedelta(days=7)).date().isoformat()

        # Get today's total cost
        cursor = conn.execute(
            "SELECT SUM(estimated_cost) as total FROM requests WHERE date(timestamp) = ?", (today,)
        )
        row = cursor.fetchone()
        today_cost = row["total"] if row else None
        if today_cost is None:
            today_cost = 0.0

        # Get last week's avg (excluding today)
        cursor = conn.execute(
            "SELECT AVG(daily_cost) as avg_cost FROM "
            "(SELECT date(timestamp) as day, SUM(estimated_cost) as daily_cost "
            "FROM requests WHERE date(timestamp) BETWEEN ? AND ? AND date(timestamp) != ? "
            "GROUP BY day)",
            (week_ago, today, today),
        )
        row = cursor.fetchone()
        last_week_avg = row["avg_cost"] if row else None
        if last_week_avg is None:
            last_week_avg = 0.0

        conn.close()

        if last_week_avg > 0 and today_cost > 2 * last_week_avg:
            n = _add_notification(
                "alert",
                "Cost Spike Detected",
                f"Today's cost (${today_cost:.2f}) is > 2x the average",
            )
            return [n]
        return []
    except Exception as e:
        logger.warning(f"Cost spike detection failed: {e}")
        return []


def _check_cache_miss_rate(storage) -> list:
    """Check if cache miss rate is >80% in recent requests."""
    try:
        db_path = storage._db_path if hasattr(storage, "_db_path") else str(storage)
        conn = sqlite3.connect(db_path)

        # Check the last 1000 requests for cache miss rate
        cursor = conn.execute(
            "SELECT COUNT(*) as total, "
            "SUM(CASE WHEN compilation_mode = 'miss' THEN 1 ELSE 0 END) as misses "
            "FROM (SELECT * FROM requests ORDER BY timestamp DESC LIMIT 1000)"
        )
        row = cursor.fetchone()
        total = row[0] if row else 0
        misses = row[1] if row else 0

        conn.close()

        if total > 0:
            miss_rate = misses / total
            if miss_rate > 0.8:
                n = _add_notification(
                    "warning", "High Cache Miss Rate", f"Cache miss rate is {miss_rate:.1%}"
                )
                return [n]
        return []
    except Exception as e:
        logger.warning(f"Cache miss detection failed: {e}")
        return []


def _check_recent_errors(storage) -> list:
    """Check if there are any errors (status_code >= 400) in recent requests."""
    try:
        db_path = storage._db_path if hasattr(storage, "_db_path") else str(storage)
        conn = sqlite3.connect(db_path)

        # Check the last 10000 requests for errors
        cursor = conn.execute(
            "SELECT COUNT(*) as error_count FROM "
            "(SELECT * FROM requests ORDER BY timestamp DESC LIMIT 10000) "
            "WHERE status_code >= 400"
        )
        row = cursor.fetchone()
        error_count = row[0] if row else 0

        conn.close()

        if error_count > 0:
            n = _add_notification("alert", "Recent Errors", f"{error_count} request(s) failed")
            return [n]
        return []
    except Exception as e:
        logger.warning(f"Error detection failed: {e}")
        return []


def _safe_list_traces(
    storage: TelemetryDB,
    limit: int,
    provider: Optional[str],
    model: Optional[str],
    agent: Optional[str],
) -> list:
    """List traces, returning empty list on error."""
    try:
        return storage.list_traces(
            limit=limit, offset=0, provider=provider, model=model, agent_id=agent, since_ts=None
        )
    except TypeError:
        # Older storage API without all params
        try:
            return storage.list_traces(limit=limit, offset=0)
        except Exception:
            return []
    except Exception as e:
        logger.warning(f"list_traces failed: {e}")
        return []


def _build_glossary_context() -> dict:
    """Return context dict for the glossary page template."""
    CATEGORIES = {
        "cost": {
            "label": "Cost",
            "icon": "💰",
            "keys": ["baseline_cost", "actual_cost", "savings", "savings_pct", "cost_per_request"],
        },
        "tokens": {
            "label": "Tokens",
            "icon": "🔢",
            "keys": [
                "raw_tokens",
                "final_tokens",
                "cache_tokens",
                "compression_ratio",
                "request_count",
            ],
        },
        "performance": {
            "label": "Performance",
            "icon": "⚡",
            "keys": ["latency_avg", "latency_p95", "latency_p99", "error_rate", "retry_rate"],
        },
        "status": {"label": "Status", "icon": "🔍", "keys": ["reconciled", "estimated"]},
    }
    TERM_LABELS = {
        "baseline_cost": "Baseline Cost",
        "actual_cost": "Actual Cost",
        "savings": "Savings",
        "savings_pct": "Savings %",
        "compression_ratio": "Compression Ratio",
        "error_rate": "Error Rate",
        "retry_rate": "Retry Rate",
        "latency_avg": "Latency (Avg)",
        "latency_p95": "Latency (p95)",
        "latency_p99": "Latency (p99)",
        "raw_tokens": "Raw Tokens",
        "final_tokens": "Final Tokens",
        "reconciled": "Reconciled",
        "estimated": "Estimated",
        "cache_tokens": "Cache Tokens",
        "request_count": "Request Count",
        "cost_per_request": "Cost / Request",
    }
    UNIT_RULES = {
        "baseline_cost": {"example": "$0.0042", "pattern": "$X.XX or $X.XXXX for small values"},
        "actual_cost": {"example": "$0.0042", "pattern": "$X.XX or $X.XXXX for small values"},
        "savings": {"example": "$1.23", "pattern": "$X.XX"},
        "savings_pct": {"example": "23.4%", "pattern": "X.X% (one decimal, no space)"},
        "cost_per_request": {"example": "$0.00042", "pattern": "$X.XXXXX (up to 5 decimal places)"},
        "compression_ratio": {
            "example": "1.4×",
            "pattern": "X.X× (multiplication sign, raw÷final)",
        },
        "error_rate": {"example": "2.3%", "pattern": "X.X%"},
        "retry_rate": {"example": "1.1%", "pattern": "X.X%"},
        "latency_avg": {"example": "142 ms", "pattern": "X ms (integer, space before unit)"},
        "latency_p95": {"example": "287 ms", "pattern": "X ms (integer, space before unit)"},
        "latency_p99": {"example": "512 ms", "pattern": "X ms (integer, space before unit)"},
        "raw_tokens": {"example": "1,234,567", "pattern": "X,XXX with locale commas"},
        "final_tokens": {"example": "876,543", "pattern": "X,XXX with locale commas"},
        "cache_tokens": {"example": "234,567", "pattern": "X,XXX with locale commas"},
        "request_count": {"example": "10,482", "pattern": "X,XXX with locale commas"},
    }
    ALL_UNIT_RULES = {
        "currency": {"example": "$0.0042", "pattern": "$X.XX or $X.XXXX for values < $0.01"},
        "latency": {"example": "142 ms", "pattern": "X ms (integer, space before unit)"},
        "percentage": {"example": "23.4%", "pattern": "X.X% (one decimal, no space before %)"},
        "token_count": {"example": "1,234,567", "pattern": "X,XXX with locale commas"},
        "ratio": {"example": "1.4×", "pattern": "X.X× (multiplication sign)"},
    }
    return {
        "glossary": _GLOSSARY_DATA_GLOBAL,
        "categories": CATEGORIES,
        "term_labels": TERM_LABELS,
        "unit_rules": UNIT_RULES,
        "all_unit_rules": ALL_UNIT_RULES,
    }


def _compare_offset(days: int, compare_range: str) -> int:
    if compare_range == "last_month":
        return 30
    if compare_range == "same_period_last_month":
        return 30
    return days


def create_dashboard_router(
    storage: TelemetryDB,
    rollups: RollupEngine,
) -> APIRouter:
    """Create the dashboard router.

    Parameters
    ----------
    storage:
        TelemetryDB instance for raw trace queries.
    rollups:
        RollupEngine instance for aggregated summaries and timeseries.

    Returns
    -------
    APIRouter
        FastAPI router — mount at /dashboard.
    """
    router = APIRouter(prefix="/dashboard")

    # Static files are mounted by the app (not router) — see create_app()

    # -----------------------------------------------------------------------
    # Helper: fetch dimension lists for filter dropdowns
    # -----------------------------------------------------------------------
    def _get_filter_options():
        """Return available providers, models, agents for filter dropdowns."""
        try:
            cur = storage._conn.cursor()
            cur.execute(
                "SELECT DISTINCT provider FROM tp_events WHERE provider != '' ORDER BY provider"
            )
            providers = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT DISTINCT model FROM tp_events WHERE model != '' ORDER BY model")
            models = [r[0] for r in cur.fetchall()]
            cur.execute(
                "SELECT DISTINCT agent_id FROM tp_events WHERE agent_id != '' ORDER BY agent_id"
            )
            agents = [r[0] for r in cur.fetchall()]
            return providers, models, agents
        except Exception:
            return [], [], []

    def _filter_ctx(days, provider, model, agent, status, compression):
        """Build shared filter context dict for all views."""
        providers, models, agents = _get_filter_options()
        return {
            "days": days,
            "filter_provider": provider or "",
            "filter_model": model or "",
            "filter_agent": agent or "",
            "filter_status": status or "all",
            "filter_compression": compression or "all",
            "providers": providers,
            "models": models,
            "agents": agents,
        }

    # Exposing the static dir path for app-level mounting:
    setattr(router, "state", {"static_dir": str(_STATIC_DIR)})

    # -----------------------------------------------------------------------
    # Root redirect
    # -----------------------------------------------------------------------

    @router.get("/", include_in_schema=False)
    async def root():
        """Serve the telemetry dashboard HTML page."""
        return RedirectResponse(url="/dashboard/finops")

    # -----------------------------------------------------------------------
    # FinOps View
    # -----------------------------------------------------------------------

    @router.get("/finops", response_class=HTMLResponse)
    async def finops(
        request: Request,
        days: int = Query(7, ge=1, le=365),
        partial: int = Query(0),
        provider: Optional[str] = Query(None),
        model: Optional[str] = Query(None),
        agent: Optional[str] = Query(None),
        compare: bool = Query(False),
        compare_range: Optional[str] = Query("previous"),
        status: Optional[str] = Query(None),
        compression: Optional[str] = Query(None),
    ):
        """FinOps dashboard — cost, savings, compression."""
        loop = asyncio.get_event_loop()

        summary = await loop.run_in_executor(_executor, rollups.get_summary, days)
        cost_ts = await loop.run_in_executor(
            _executor, rollups.get_timeseries, "cost", "day", days, None, None, None
        )
        req_ts = await loop.run_in_executor(
            _executor, rollups.get_timeseries, "requests", "day", days, None, None, None
        )
        savings_ts = await loop.run_in_executor(
            _executor, rollups.get_timeseries, "savings", "day", days, None, None, None
        )

        # Savings summary + milestones
        try:
            from tokenpak.telemetry.milestones import (
                get_pending_milestones,
                get_savings_history,
                get_savings_summary,
            )

            savings_summary = await loop.run_in_executor(_executor, get_savings_summary, None)
            savings_history = await loop.run_in_executor(
                _executor, lambda: get_savings_history(days=days)
            )
            pending_milestones = await loop.run_in_executor(_executor, get_pending_milestones, None)
        except Exception:
            from types import SimpleNamespace

            savings_summary = SimpleNamespace(  # type: ignore[assignment]
                lifetime_savings=0,
                savings_30d=0,
                savings_7d=0,
                trend_pct=0,
                efficiency_score=0,
                compression_pct=0,
                total_requests=0,
            )
            savings_history = []
            pending_milestones = []

        totals = summary.get("totals", {})
        ctx = {
            "request": request,
            "view": "finops",
            "summary": totals,  # Alias for templates that expect summary.total_*
            "totals": totals,
            "by_provider": summary.get("by_provider", []),
            "by_model": summary.get("by_model", []),
            "by_agent": summary.get("by_agent", []),
            "cost_timeseries": cost_ts,
            "requests_timeseries": req_ts,
            "savings_timeseries": savings_ts,
            "cost_components": rollups.get_cost_components(days),
            "cache_stats": rollups.get_cache_stats(days),
            "savings_summary": savings_summary,
            "savings_history": [
                {"date": p.date, "daily": p.daily_savings, "cumulative": p.cumulative_savings}
                for p in savings_history
            ],
            "pending_milestones": [
                {"id": m.id, "label": m.label, "milestone_type": m.milestone_type}
                for m in pending_milestones
            ],
            **_filter_ctx(days, provider, model, agent, status, compression),
        }

        tmpl = "finops_partial.html" if partial else "finops.html"
        return templates.TemplateResponse(request, tmpl, ctx)

    # -----------------------------------------------------------------------
    # Engineering View
    # -----------------------------------------------------------------------

    @router.get("/engineering", response_class=HTMLResponse)
    async def engineering(
        request: Request,
        days: int = Query(7, ge=1, le=365),
        partial: int = Query(0),
        provider: Optional[str] = Query(None),
        model: Optional[str] = Query(None),
        agent: Optional[str] = Query(None),
        status: Optional[str] = Query(None),
        compression: Optional[str] = Query(None),
    ):
        """Engineering dashboard — tokens, requests, agents."""
        loop = asyncio.get_event_loop()

        summary = await loop.run_in_executor(_executor, rollups.get_summary, days)
        token_ts = await loop.run_in_executor(
            _executor, rollups.get_timeseries, "tokens", "day", days, None, None, None
        )
        req_ts = await loop.run_in_executor(
            _executor, rollups.get_timeseries, "requests", "day", days, None, None, None
        )

        totals = summary.get("totals", {})
        # Top token offenders (largest requests) for engineering drilldown
        top_traces_raw = await loop.run_in_executor(
            _executor, _safe_list_traces, storage, 50, provider, model, agent
        )
        top_traces = sorted(
            top_traces_raw,
            key=lambda t: t.get("input_billed") or t.get("total_tokens_billed") or 0,
            reverse=True,
        )[:20]

        ctx = {
            "request": request,
            "view": "engineering",
            "summary": totals,  # Alias for templates that expect summary.total_tokens
            "totals": totals,
            "by_provider": summary.get("by_provider", []),
            "by_model": summary.get("by_model", []),
            "by_agent": summary.get("by_agent", []),
            "token_timeseries": token_ts,
            "request_timeseries": req_ts,
            "latency_timeseries": [],  # Placeholder - latency tracking not yet implemented
            "error_timeseries": [],  # Placeholder - error tracking not yet implemented
            "token_composition": [],  # Placeholder - composition breakdown not yet implemented
            "top_traces": top_traces,
            **_filter_ctx(days, provider, model, agent, status, compression),
        }

        tmpl = "engineering_partial.html" if partial else "engineering.html"
        return templates.TemplateResponse(request, tmpl, ctx)

    # -----------------------------------------------------------------------
    # Integration View
    # -----------------------------------------------------------------------

    @router.get("/integration", response_class=HTMLResponse)
    async def integration(request: Request):
        """Integration settings page."""
        ctx = {
            "request": request,
            "view": "integration",
            "days": 7,
            "filter_provider": "",
            "filter_model": "",
            "filter_agent": "",
            "filter_status": "all",
            "filter_compression": "all",
        }
        return templates.TemplateResponse(request, "integration.html", ctx)

    # -----------------------------------------------------------------------
    # Audit View
    # -----------------------------------------------------------------------

    @router.get("/export/csv")
    async def export_csv(
        days: int = Query(default=7, ge=1, le=365),
        provider: Optional[str] = Query(default=""),
        model: Optional[str] = Query(default=""),
        agent: Optional[str] = Query(default=""),
        status: Optional[str] = Query(default="all"),
        limit: int = Query(default=10000, ge=1, le=10000),
    ):
        """Export traces as CSV."""
        import asyncio
        import datetime

        from fastapi.responses import Response

        loop = asyncio.get_event_loop()
        traces = await loop.run_in_executor(
            _executor,
            _safe_list_traces,
            storage,
            limit,
            provider if provider else None,
            model if model else None,
            agent if agent else None,
        )

        filters = {
            "days": days,
            "provider": provider,
            "model": model,
            "agent": agent,
            "status": status,
        }
        csv_content = _build_csv_export(traces, filters)

        filename = f"tokenpak-export-{datetime.date.today().isoformat()}.csv"
        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/export/json")
    async def export_json(
        days: int = Query(default=7, ge=1, le=365),
        provider: Optional[str] = Query(default=""),
        model: Optional[str] = Query(default=""),
        agent: Optional[str] = Query(default=""),
        status: Optional[str] = Query(default="all"),
        limit: int = Query(default=10000, ge=1, le=10000),
    ):
        """Export traces as JSON."""
        import asyncio
        import datetime

        from fastapi.responses import Response

        loop = asyncio.get_event_loop()
        traces = await loop.run_in_executor(
            _executor,
            _safe_list_traces,
            storage,
            limit,
            provider if provider else None,
            model if model else None,
            agent if agent else None,
        )

        filters = {
            "days": days,
            "provider": provider,
            "model": model,
            "agent": agent,
            "status": status,
        }
        json_content = _build_json_export(traces, filters)

        filename = f"tokenpak-export-{datetime.date.today().isoformat()}.json"
        return Response(
            content=json_content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/export/trace/{trace_id}")
    async def export_trace(trace_id: str):
        """Export single trace as JSON."""
        import asyncio
        import json

        from fastapi.responses import Response

        loop = asyncio.get_event_loop()
        trace = await loop.run_in_executor(_executor, storage.get_trace, trace_id)

        if not trace:
            return Response(
                content='{"error": "Trace not found"}',
                status_code=404,
                media_type="application/json",
            )

        trace_data = _format_trace_for_export(trace)
        json_content = json.dumps(
            {
                "export_metadata": _export_metadata({"trace_id": trace_id}, 1),
                "trace": trace_data,
            },
            indent=2,
        )

        filename = f"tokenpak-trace-{trace_id}.json"
        return Response(
            content=json_content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/audit", response_class=HTMLResponse)
    async def audit(
        request: Request,
        days: int = Query(7, ge=1, le=365),
        partial: int = Query(0),
        provider: Optional[str] = Query(None),
        model: Optional[str] = Query(None),
        agent: Optional[str] = Query(None),
        status: Optional[str] = Query(None),
        compression: Optional[str] = Query(None),
    ):
        """Audit dashboard — trace timeline, filters, drilldown."""
        loop = asyncio.get_event_loop()

        summary = await loop.run_in_executor(_executor, rollups.get_summary, days)
        traces = await loop.run_in_executor(
            _executor, _safe_list_traces, storage, 100, provider, model, agent
        )

        # Pagination defaults (100 traces per page)
        page = 1
        per_page = 100
        total_traces = len(traces)
        total_pages = max(1, (total_traces + per_page - 1) // per_page)

        ctx = {
            "request": request,
            "view": "audit",
            "totals": summary.get("totals", {}),
            "traces": traces,
            "trace_count": total_traces,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            **_filter_ctx(days, provider, model, agent, status, compression),
        }

        tmpl = "audit_partial.html" if partial else "audit.html"
        return templates.TemplateResponse(request, tmpl, ctx)

    @router.get("/audit/trace/{trace_id}", response_class=HTMLResponse)
    async def audit_trace_detail(
        request: Request,
        trace_id: str,
        days: int = Query(7),
    ):
        """HTMX drilldown — full trace detail with segments and hash chain."""
        loop = asyncio.get_event_loop()

        trace_data = await loop.run_in_executor(_executor, storage.get_trace, trace_id)
        event = trace_data.get("event") or {}
        usage = trace_data.get("usage") or {}
        cost = trace_data.get("cost") or {}
        segments = trace_data.get("segments") or []

        ctx = {
            "request": request,
            "trace": {"trace_id": trace_id, **event},
            "event": event,
            "usage": usage,
            "cost": cost,
            "segments": sorted(segments, key=lambda s: s.get("order", 0)),
            "days": days,
        }

        return templates.TemplateResponse(request, "trace_detail.html", ctx)

    @router.get("/executive-summary")
    async def executive_summary_api(
        days: int = Query(default=7, ge=1, le=365),
        provider: Optional[str] = Query(default=""),
        model: Optional[str] = Query(default=""),
        format: str = Query(default="paragraph"),
    ):
        """Generate executive summary in paragraph or bullet format."""
        import asyncio
        import functools

        loop = asyncio.get_event_loop()
        summary = await loop.run_in_executor(
            _executor,
            functools.partial(
                _generate_executive_summary,
                rollups,
                days,
                provider or None,
                model or None,
                format,
            ),
        )
        return {"summary": summary, "format": format, "days": days}

    # -----------------------------------------------------------------------
    # Settings View
    # -----------------------------------------------------------------------

    @router.get("/settings", response_class=HTMLResponse)
    @router.get("/settings/{section}", response_class=HTMLResponse)
    async def settings_view(request: Request, section: str = "personal"):
        """Settings page with 8 sections."""
        valid_sections = [
            "personal",
            "dashboard",
            "data",
            "pricing",
            "alerts",
            "access",
            "integrations",
            "system",
        ]
        if section not in valid_sections:
            section = "personal"
        ctx = {
            "request": request,
            "view": "settings",
            "section": section,
            "sections": valid_sections,
        }
        return templates.TemplateResponse(request, "settings.html", ctx)

    @router.post("/settings/save", response_class=HTMLResponse)
    async def settings_save(request: Request):
        """Server-side settings save (stub; most prefs use localStorage)."""
        try:
            await request.json()
        except Exception:
            pass
        return HTMLResponse(content='{"status":"ok"}', media_type="application/json")

    # --- Notification endpoints ---
    @router.get("/notifications")
    async def get_notifications(limit: int = Query(20)):
        """Get recent notifications."""
        notifs = list(_NOTIFICATIONS)[:limit]
        unread = sum(1 for n in _NOTIFICATIONS if not n["read"])
        return {"notifications": notifs, "unread": unread}

    @router.post("/notifications/{notification_id}/read")
    async def mark_notification_read(notification_id: str):
        """Mark a notification as read."""
        for n in _NOTIFICATIONS:
            if n["id"] == notification_id:
                n["read"] = True
                return {"ok": True}
        return {"ok": False}, 404

    @router.post("/notifications/read-all")
    async def mark_all_read():
        """Mark all notifications as read."""
        count = 0
        for n in _NOTIFICATIONS:
            if not n["read"]:
                n["read"] = True
                count += 1
        return {"ok": True, "marked": count}

    @router.get("/glossary", response_class=HTMLResponse)
    async def glossary_view(request: Request):
        """Terminology glossary page — standardized terms and definitions."""
        ctx = _build_glossary_context()
        ctx["request"] = request
        ctx["view"] = "glossary"
        return templates.TemplateResponse("glossary.html", ctx)

    return router


# ─────────────────────────────────────────────────────────────────────────────
# Executive Summary generator
# ─────────────────────────────────────────────────────────────────────────────


def _generate_executive_summary(
    rollups: Any,
    days: int,
    provider: str | None = None,
    model: str | None = None,
    format: str = "paragraph",
):
    """Generate executive summary in paragraph or bullet format."""
    import datetime

    # Fetch current period stats
    try:
        cur_stats = rollups.get_summary_stats(days=days)
    except Exception:
        cur_stats = {}

    total_cost = float(cur_stats.get("total_cost_usd", 0) or 0)
    total_savings = float(cur_stats.get("total_savings_usd", 0) or 0)
    savings_pct = float(cur_stats.get("savings_pct", 0) or 0)
    requests = int(cur_stats.get("request_count", 0) or 0)

    # Previous period comparison
    prev_stats = {}
    try:
        prev_stats = rollups.get_summary_stats(days=days)  # Would need offset parameter
    except Exception:
        pass
    prev_cost = float(prev_stats.get("total_cost_usd", 0) or 0)

    cost_delta_pct: float = 0
    if prev_cost > 0:
        cost_delta_pct = round((total_cost - prev_cost) / prev_cost * 100, 1)

    # Top driver (provider or model)
    top_driver = "Unknown"
    top_driver_pct = 0
    try:
        by_prov = rollups.get_cost_by_provider(days=days) or []
        if by_prov:
            top = max(by_prov, key=lambda x: float(x.get("cost_usd", 0) or 0))
            top_driver = top.get("provider", "Unknown")
            top_cost = float(top.get("cost_usd", 0) or 0)
            top_driver_pct = round(top_cost / total_cost * 100) if total_cost > 0 else 0
    except Exception:
        pass

    # Date range
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days - 1)
    date_range = f"{start.strftime('%b %d')}-{end.strftime('%d, %Y')}"

    # Risk factors
    risks = []
    error_rate = float(cur_stats.get("error_rate", 0) or 0)
    if error_rate > 0.05:
        risks.append(f"Error rate elevated at {error_rate * 100:.1f}%")

    # Trend label
    if cost_delta_pct > 5:
        trend = f"increased {abs(cost_delta_pct)}%"
    elif cost_delta_pct < -5:
        trend = f"decreased {abs(cost_delta_pct)}%"
    else:
        trend = "remained stable"

    # Generate summary
    if format == "bullets":
        lines = [
            f"📊 **Executive Summary** ({date_range})",
            "",
            f"• **Total Spend:** ${total_cost:,.2f}",
            f"• **Savings:** ${total_savings:,.2f} ({savings_pct:.0f}% of baseline)",
        ]

        if cost_delta_pct != 0:
            direction = "↓ improving" if cost_delta_pct < 0 else "↑ increasing"
            lines.append(f"• **vs Last Period:** {cost_delta_pct:+.0f}% ({direction})")

        if top_driver != "Unknown":
            lines.append(f"• **Top Driver:** {top_driver} ({top_driver_pct}% of spend)")

        if savings_pct > 0:
            compression_ratio = 1 / (1 - savings_pct / 100) if savings_pct < 100 else 1
            lines.append(f"• **Efficiency:** Compression ratio {compression_ratio:.1f}:1")

        if risks:
            lines.append(f"• **Risks:** {'; '.join(risks)}")
        else:
            lines.append("• **Status:** No anomalies detected")

        return "\n".join(lines)

    else:  # paragraph
        para = f"For {date_range}, total AI spend was ${total_cost:,.2f}"

        if total_savings > 0:
            para += f" with compression saving ${total_savings:,.2f} ({savings_pct:.0f}%)"

        para += ". "

        if cost_delta_pct != 0:
            para += f"Costs {trend} versus the prior period"
            if abs(cost_delta_pct) > 10:
                para += f", primarily due to {'reduced usage' if cost_delta_pct < 0 else 'increased usage'}"
            para += ". "

        if top_driver != "Unknown":
            para += f"{top_driver} accounts for {top_driver_pct}% of total spend. "

        if risks:
            para += f"Attention needed: {'; '.join(risks)}. "
        else:
            para += "No significant anomalies detected. "

        para += f"Total requests: {requests:,}."

        return para


# ─────────────────────────────────────────────────────────────────────────────
# Export System helpers
# ─────────────────────────────────────────────────────────────────────────────


def _export_metadata(filters, record_count):
    """Generate export metadata header."""
    import datetime

    return {
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "filters": {k: v for k, v in filters.items() if v},
        "pricing_version": "2026.02",
        "record_count": record_count,
        "data_source_note": "Cost estimates based on token counts × model pricing. Not verified against billing.",
    }


def _format_trace_for_export(event):
    """Convert a telemetry event to export-ready dict."""
    return {
        "trace_id": event.get("trace_id", ""),
        "timestamp": event.get("timestamp", ""),
        "provider": event.get("provider", ""),
        "model": event.get("model", ""),
        "tokens": {
            "raw_input": event.get("total_input_tokens", 0),
            "compressed_input": event.get("compressed_tokens", 0),
            "output": event.get("output_tokens", 0),
        },
        "cost": {
            "baseline": round(float(event.get("baseline_cost_usd", 0) or 0), 4),
            "actual": round(float(event.get("cost_usd", 0) or 0), 4),
            "savings": round(float(event.get("savings_usd", 0) or 0), 4),
            "savings_pct": round(float(event.get("savings_pct", 0) or 0), 1),
        },
        "latency_ms": event.get("duration_ms", 0),
        "status": event.get("status", "unknown"),
        "data_source": "estimated",
    }


def _build_csv_export(traces, filters):
    """Generate CSV content as string."""
    import csv
    import datetime
    import io

    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)

    # Metadata comment rows
    writer.writerow(["# TokenPak Export"])
    writer.writerow(["# Generated:", datetime.datetime.utcnow().isoformat() + "Z"])
    writer.writerow(["# Filters:", str({k: v for k, v in filters.items() if v})])
    writer.writerow(["# Pricing:", "2026.02"])
    writer.writerow([])

    # Header row
    writer.writerow(
        [
            "trace_id",
            "timestamp",
            "provider",
            "model",
            "raw_input_tokens",
            "compressed_input_tokens",
            "output_tokens",
            "baseline_cost",
            "actual_cost",
            "savings",
            "savings_pct",
            "latency_ms",
            "status",
            "data_source",
        ]
    )

    # Data rows
    for t in traces:
        ex = _format_trace_for_export(t)
        writer.writerow(
            [
                ex["trace_id"],
                ex["timestamp"],
                ex["provider"],
                ex["model"],
                ex["tokens"]["raw_input"],
                ex["tokens"]["compressed_input"],
                ex["tokens"]["output"],
                ex["cost"]["baseline"],
                ex["cost"]["actual"],
                ex["cost"]["savings"],
                ex["cost"]["savings_pct"],
                ex["latency_ms"],
                ex["status"],
                ex["data_source"],
            ]
        )

    return output.getvalue()


def _build_json_export(traces, filters):
    """Generate JSON export with metadata."""
    import json

    data = [_format_trace_for_export(t) for t in traces]
    return json.dumps(
        {
            "export_metadata": _export_metadata(filters, len(data)),
            "data": data,
        },
        indent=2,
    )
