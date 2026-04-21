"""FastAPI router for TokenPak telemetry query API."""

from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path

try:
    from fastapi import APIRouter, Query

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore
    Query = None  # type: ignore

from tokenpak.telemetry.query import (
    get_cost_summary,
    get_daily_trend,
    get_model_usage,
    get_recent_events,
    get_savings_report,
)


def _get_db_path():
    p = os.environ.get("TOKENPAK_DB_PATH")
    return Path(p) if p else Path(__file__).parent.parent.parent / "telemetry.db"


def _to_json(obj):
    if isinstance(obj, list):
        return [asdict(i) if hasattr(i, "__dataclass_fields__") else i for i in obj]
    return asdict(obj) if hasattr(obj, "__dataclass_fields__") else obj


if FASTAPI_AVAILABLE:
    router = APIRouter(tags=["telemetry"])  # type: ignore

    @router.get("/cost-summary")
    def api_cost_summary(days: int = Query(default=30, ge=1, le=365)):
        """Return aggregated cost summary across all sessions."""
        return _to_json(get_cost_summary(db_path=_get_db_path(), days=days))

    @router.get("/model-usage")
    def api_model_usage(days: int = Query(default=30, ge=1, le=365)):
        """Return per-model token usage statistics."""
        return _to_json(get_model_usage(db_path=_get_db_path(), days=days))

    @router.get("/savings")
    def api_savings(days: int = Query(default=30, ge=1, le=365)):
        """Return token savings report (raw vs compressed)."""
        return _to_json(get_savings_report(db_path=_get_db_path(), days=days))

    @router.get("/events")
    def api_events(limit: int = Query(default=50, ge=1, le=1000)):
        """Return recent telemetry events with optional filtering."""
        return get_recent_events(db_path=_get_db_path(), limit=limit)

    @router.get("/daily-trend")
    def api_daily_trend(days: int = Query(default=30, ge=1, le=365)):
        """Return daily aggregated usage trends."""
        return _to_json(get_daily_trend(db_path=_get_db_path(), days=days))

    @router.get("/health")
    def api_health():
        """Health check: return service status and DB stats."""
        import sqlite3

        db = _get_db_path()
        exists = db.exists()
        sz, ev, co = 0.0, 0, 0
        if exists:
            sz = db.stat().st_size / 1024 / 1024
            try:
                c = sqlite3.connect(str(db))
                cur = c.cursor()
                cur.execute("SELECT COUNT(*) FROM tp_events")
                ev = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM tp_costs")
                co = cur.fetchone()[0]
                c.close()
            except Exception as e:
                return {
                    "status": "error",
                    "db_path": str(db),
                    "db_exists": exists,
                    "db_size_mb": round(sz, 2),
                    "error": str(e),
                    "event_count": 0,
                    "cost_count": 0,
                }
        return {
            "status": "ok" if exists else "no_db",
            "db_path": str(db),
            "db_exists": exists,
            "db_size_mb": round(sz, 2),
            "event_count": ev,
            "cost_count": co,
        }

    @router.get("/savings/summary")
    def api_savings_summary():
        """Lifetime/30d/7d savings with trend and efficiency score."""
        from tokenpak.telemetry.milestones import get_savings_summary

        s = get_savings_summary(db_path=_get_db_path())
        return {
            "lifetime_savings": s.lifetime_savings,
            "savings_30d": s.savings_30d,
            "savings_7d": s.savings_7d,
            "trend_pct": s.trend_pct,
            "efficiency_score": s.efficiency_score,
            "compression_pct": s.compression_pct,
            "total_requests": s.total_requests,
        }

    @router.get("/savings/history")
    def api_savings_history(days: int = Query(default=90, ge=1, le=365)):
        """Daily savings + cumulative sum for chart rendering."""
        from tokenpak.telemetry.milestones import get_savings_history

        return [
            {
                "date": p.date,
                "daily_savings": p.daily_savings,
                "cumulative_savings": p.cumulative_savings,
            }
            for p in get_savings_history(db_path=_get_db_path(), days=days)
        ]

    @router.get("/milestones")
    def api_milestones(all: bool = Query(default=False)):
        """Return pending (unacknowledged) milestones, or all milestones."""
        from tokenpak.telemetry.milestones import get_milestone_history, get_pending_milestones

        fn = get_milestone_history if all else get_pending_milestones
        return [
            {
                "id": m.id,
                "milestone_type": m.milestone_type,
                "threshold": m.threshold,
                "label": m.label,
                "reached_at": m.reached_at,
                "acknowledged": m.acknowledged,
            }
            for m in fn(db_path=_get_db_path())
        ]

    @router.post("/milestones/{milestone_id}/acknowledge")
    def api_acknowledge_milestone(milestone_id: int):
        """Mark a milestone as acknowledged."""
        from tokenpak.telemetry.milestones import acknowledge_milestone

        updated = acknowledge_milestone(milestone_id, db_path=_get_db_path())
        return {"updated": updated, "id": milestone_id}

    @router.post("/milestones/check")
    def api_check_milestones():
        """Detect and record new milestones. Returns newly created."""
        from tokenpak.telemetry.milestones import check_and_create_milestones

        new_ms = check_and_create_milestones(db_path=_get_db_path())
        return [
            {
                "id": m.id,
                "milestone_type": m.milestone_type,
                "threshold": m.threshold,
                "label": m.label,
                "reached_at": m.reached_at,
            }
            for m in new_ms
        ]

else:
    router = None  # type: ignore


def create_app():
    """Create and return the FastAPI application with all telemetry routes."""
    if not FASTAPI_AVAILABLE:
        raise ImportError("FastAPI required")
    from fastapi import FastAPI

    app = FastAPI(title="TokenPak Telemetry API", version="0.1.0")
    app.include_router(router, prefix="/api/v1/telemetry")
    return app
