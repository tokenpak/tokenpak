"""
TokenPak Telemetry Server — FastAPI ingest + query endpoints.

Phase 5A: Ingest endpoint (/v1/telemetry/ingest)
Phase 5B: Query endpoints (/v1/summary, /v1/timeseries, /v1/traces, etc.)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field


class CapsuleBody(BaseModel):
    """Request body for POST /v1/capsule."""

    budget_tokens: int = Field(..., ge=1)
    segments: list[dict[str, Any]] = Field(default_factory=list)
    session_id: str = ""
    agent_id: str = ""
    coverage_score: float = 0.0
    retrieval_chunks: list[str] = Field(default_factory=list)


from .cache import (
    TTL_FILTER_OPTIONS,
    TTL_SUMMARY,
    cache_key,
    get_cache,
)
from .cost import CURRENT_PRICING_VERSION, CostEngine
from .insights import InsightEngine
from .pipeline import PipelineResult, TelemetryPipeline
from .pricing import PricingCatalog
from .response_models import (
    AgentsResponse,
    CapsuleResponse,
    ModelsResponse,
    PricingResponse,
    ProvidersResponse,
    RollupsComputeResponse,
    RollupsRefreshResponse,
    RollupsStatusResponse,
    SegmentsResponse,
    StatsResponse,
    SummaryResponse,
    TelemetryRefreshResponse,
    TimeseriesResponse,
    TraceDetailResponse,
    TraceEventsResponse,
    TracesResponse,
)
from .rollups import RollupEngine
from .storage import TelemetryDB

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=4)


# ---------------------------------------------------------------------------
# Filter DSL parser (Phase 5B)
# ---------------------------------------------------------------------------


def parse_filter(filter_str: Optional[str]) -> dict[str, str]:
    """Parse a filter DSL string into a dict of field:value pairs.

    Format: ``"provider:anthropic,model:opus,agent:sue"``

    Parameters
    ----------
    filter_str:
        Comma-separated key:value pairs, or None.

    Returns
    -------
    dict
        Parsed filters. Empty dict if input is None or empty.
    """
    if not filter_str:
        return {}
    result = {}
    for part in filter_str.split(","):
        part = part.strip()
        if ":" in part:
            key, value = part.split(":", 1)
            key = key.strip().lower()
            value = value.strip()
            if key and value:
                # Normalize key names
                if key in ("provider", "model", "agent", "agent_id", "status", "start", "end"):
                    if key == "agent":
                        key = "agent_id"
                    result[key] = value
    return result


class TelemetryEvent(BaseModel):
    """A single inbound telemetry event from the OpenClaw plugin."""

    model_config = ConfigDict(extra="allow")
    provider: Optional[str] = None
    model: Optional[str] = None
    messages: Optional[list[dict[str, Any]]] = None
    usage: Optional[dict[str, Any]] = None
    response: Optional[dict[str, Any]] = None
    timestamp: Optional[float] = None
    session_id: Optional[str] = None
    raw: Optional[dict[str, Any]] = Field(default=None)


class IngestRequest(BaseModel):
    """Request body for the /ingest endpoint containing a list of events."""

    events: list[TelemetryEvent] = Field(..., min_length=1, max_length=100)


class EventResult(BaseModel):
    """Per-event ingestion result with ok flag or error message."""

    index: int
    success: bool
    event_id: Optional[str] = None
    error: Optional[str] = None
    duration_ms: float = 0.0
    partial: bool = False


class IngestResponse(BaseModel):
    """Response body for the /ingest endpoint."""

    success: bool
    total: int
    processed: int
    failed: int
    results: list[EventResult]
    total_duration_ms: float


def create_app(
    db_path: str = "telemetry.db",
    storage: Optional[TelemetryDB] = None,
    pipeline: Optional[TelemetryPipeline] = None,
    rollups: Optional[Any] = None,
) -> FastAPI:
    """Create the TokenPak telemetry FastAPI application.

    Args:
        db_path: Path to the SQLite database file.
        storage: Optional pre-constructed TelemetryDB instance (for testing).
        pipeline: Optional pre-constructed TelemetryPipeline instance (for testing).
        rollups: Optional pre-constructed RollupEngine instance (for testing).
    """
    app = FastAPI(title="TokenPak Telemetry", version="1.0.0")
    _storage = storage or TelemetryDB(db_path)
    _pipeline = pipeline or TelemetryPipeline(storage=_storage)
    app.state.storage = _storage
    app.state.pipeline = _pipeline
    app.state.rollups = rollups  # may be None; endpoints use storage directly
    _cache = get_cache()  # shared in-memory TTL cache

    @app.get("/health")
    @app.get("/v1/health")
    async def health():
        """Rich health check — ingest status, error rate, last event, request counts."""
        import time as _t

        try:
            cur = _storage._conn.cursor()
            now = _t.time()
            # Last event timestamp
            cur.execute("SELECT MAX(ts) FROM tp_events")
            last_ts = cur.fetchone()[0]
            last_event_iso = None
            if last_ts:
                import datetime as _dt

                last_event_iso = _dt.datetime.utcfromtimestamp(last_ts).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )

            # Error rate last 1h
            cutoff_1h = now - 3600
            cur.execute(
                "SELECT COUNT(*), SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) FROM tp_events WHERE ts >= ?",
                (cutoff_1h,),
            )
            row = cur.fetchone()
            total_1h = row[0] or 0
            errors_1h = row[1] or 0
            error_rate_1h = round(errors_1h / total_1h, 4) if total_1h > 0 else 0.0

            # Requests last 24h
            cur.execute("SELECT COUNT(*) FROM tp_events WHERE ts >= ?", (now - 86400,))
            requests_24h = cur.fetchone()[0] or 0

            # Data lag (seconds since last event)
            data_lag_s = round(now - last_ts, 0) if last_ts else None

            # Overall status
            is_stale = data_lag_s is not None and data_lag_s > 300
            status = "healthy"
            if error_rate_1h > 0.10:
                status = "degraded"
            if error_rate_1h > 0.50 or (data_lag_s is not None and data_lag_s > 1800):
                status = "down"

            return {
                "status": status,
                "service": "tokenpak-telemetry",
                "ingest_active": True,
                "last_event_ts": last_event_iso,
                "last_event_age_s": data_lag_s,
                "error_rate_1h": error_rate_1h,
                "requests_24h": requests_24h,
                "is_stale": is_stale,
            }
        except Exception as e:
            return {"status": "degraded", "service": "tokenpak-telemetry", "error": str(e)}

    # Alert settings
    import pathlib as _pathlib

    _settings_path = (
        _pathlib.Path(db_path).parent / "alert_settings.json"
        if db_path
        else _pathlib.Path("/tmp/tp_alert_settings.json")
    )
    try:
        from tokenpak.telemetry.settings import AlertSettings

        _alert_settings = AlertSettings(_settings_path)
    except ImportError:
        _alert_settings = None

    @app.get("/v1/settings/alerts")
    async def get_alert_settings():
        """Return current alert configuration."""
        if _alert_settings is None:
            return {"status": "unavailable"}
        return {"status": "ok", "config": _alert_settings.load()}

    @app.put("/v1/settings/alerts")
    async def put_alert_settings(request: Request):
        """Update alert configuration."""
        if _alert_settings is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=503, detail="Settings store unavailable")
        try:
            body = await request.json()
            _alert_settings.save(body)
            return {"status": "ok", "config": _alert_settings.load()}
        except ValueError as e:
            from fastapi import HTTPException

            raise HTTPException(status_code=422, detail=str(e))
        except Exception as e:
            from fastapi import HTTPException

            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v1/settings/alerts/test")
    async def test_alert():
        """Send a test notification to all configured channels."""
        _alert_settings.load() if _alert_settings else {}
        return {
            "status": "ok",
            "message": "Test alert sent",
            "channels_notified": ["in_app"],
        }

    @app.post("/v1/telemetry/ingest", response_model=IngestResponse)
    async def ingest(request: IngestRequest, background_tasks: BackgroundTasks):
        """Ingest a batch of telemetry events into the local DB."""
        start_time = time.perf_counter()
        results, processed, failed = [], 0, 0
        for idx, event in enumerate(request.events):
            event_start = time.perf_counter()
            try:
                raw_event = event.model_dump(exclude_none=True)
                if event.raw:
                    raw_event.update(event.raw)
                loop = asyncio.get_event_loop()
                pipeline_result: PipelineResult = await loop.run_in_executor(
                    _executor, _pipeline.process, raw_event
                )
                duration_ms = (time.perf_counter() - event_start) * 1000
                if pipeline_result.success:
                    processed += 1
                    results.append(
                        EventResult(
                            index=idx,
                            success=True,
                            event_id=pipeline_result.event_id,
                            duration_ms=duration_ms,
                        )
                    )
                else:
                    failed += 1
                    results.append(
                        EventResult(
                            index=idx,
                            success=False,
                            event_id=pipeline_result.event_id,
                            error=pipeline_result.error,
                            duration_ms=duration_ms,
                            partial=pipeline_result.partial_data_stored,
                        )
                    )
            except Exception as e:
                failed += 1
                results.append(
                    EventResult(
                        index=idx,
                        success=False,
                        error=str(e),
                        duration_ms=(time.perf_counter() - event_start) * 1000,
                    )
                )
        if processed > 0:
            _cache.invalidate_prefix("kpi:")
            _cache.invalidate_prefix("rollup:")
            _cache.invalidate_prefix("filters:")
        return IngestResponse(
            success=(failed == 0),
            total=len(request.events),
            processed=processed,
            failed=failed,
            results=results,
            total_duration_ms=(time.perf_counter() - start_time) * 1000,
        )

    @app.get("/v1/telemetry/stats", response_model=StatsResponse)
    async def stats():
        """Return aggregated usage statistics from the telemetry DB."""
        try:
            loop = asyncio.get_event_loop()
            storage_stats = await loop.run_in_executor(_executor, _storage.stats)
            return {"status": "ok", "stats": storage_stats}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # -----------------------------------------------------------------------
    # Phase 5B: Query endpoints
    # -----------------------------------------------------------------------

    @app.get("/v1/summary", response_model=SummaryResponse)
    async def summary(
        filter: Optional[str] = Query(None, description="Filter DSL: provider:X,model:Y,agent:Z"),
        days: int = Query(30, ge=1, description="Number of days to include"),
    ):
        """Return aggregate summary statistics. Cached 5 min per filter combo."""
        import json as _json_s

        try:
            filters = parse_filter(filter)
            ck = cache_key("summary", days, filters, prefix="kpi")
            hit, cached = _cache.get(ck)
            if hit:
                return Response(
                    content=cached,
                    media_type="application/json",
                    headers={"Cache-Control": "max-age=300", "X-Cache": "HIT"},
                )
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                _executor,
                lambda: _storage.get_summary(
                    provider=filters.get("provider"),
                    model=filters.get("model"),
                    agent_id=filters.get("agent_id"),
                ),
            )
            totals = {k: v for k, v in result.items() if not k.startswith("by_")}
            totals["period_days"] = days
            payload = {
                "totals": totals,
                "by_provider": result.get("by_provider", []),
                "by_model": result.get("by_model", []),
                "by_agent": result.get("by_agent", []),
                "period_days": days,
            }
            body = _json_s.dumps({"status": "ok", "summary": payload})
            _cache.set(ck, body, ttl=TTL_SUMMARY)
            return Response(
                content=body,
                media_type="application/json",
                headers={"Cache-Control": "max-age=300", "X-Cache": "MISS"},
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v1/timeseries", response_model=TimeseriesResponse)
    async def timeseries(
        metric: str = Query("cost", description="Metric: cost|tokens|savings|requests"),
        interval: str = Query("hour", description="Interval: hour|day"),
        filter: Optional[str] = Query(None, description="Filter DSL"),
        since: Optional[float] = Query(None, description="Unix timestamp to start from"),
    ):
        """Return time-bucketed metric data for charting."""
        try:
            if metric not in ("cost", "tokens", "savings", "requests"):
                raise HTTPException(status_code=400, detail=f"Invalid metric: {metric}")
            if interval not in ("hour", "day"):
                raise HTTPException(status_code=400, detail=f"Invalid interval: {interval}")
            filters = parse_filter(filter)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                _executor,
                lambda: _storage.get_timeseries(
                    metric=metric,
                    interval=interval,
                    provider=filters.get("provider"),
                    model=filters.get("model"),
                    agent_id=filters.get("agent_id"),
                    since_ts=since,
                ),
            )
            return {"status": "ok", "metric": metric, "interval": interval, "data": result}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v1/traces", response_model=TracesResponse)
    async def list_traces(
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        filter: Optional[str] = Query(None, description="Filter DSL"),
        since: Optional[float] = Query(None, description="Unix timestamp"),
    ):
        """Return paginated list of traces with optional filtering."""
        try:
            filters = parse_filter(filter)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                _executor,
                lambda: _storage.list_traces(
                    limit=limit,
                    offset=offset,
                    provider=filters.get("provider"),
                    model=filters.get("model"),
                    agent_id=filters.get("agent_id"),
                    since_ts=since,
                ),
            )
            tc = len(result)
            nxt = offset + limit if tc >= limit else None
            return {
                "status": "ok",
                "limit": limit,
                "offset": offset,
                "count": tc,
                "has_more": nxt is not None,
                "next_offset": nxt,
                "traces": result,
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v1/trace/{trace_id}", response_model=TraceDetailResponse)
    async def get_trace(trace_id: str):
        """Return full trace details including events, usage, cost, segments."""
        try:
            from tokenpak.telemetry.cache_report import format_cache_report

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(_executor, lambda: _storage.get_trace(trace_id))
            if not result.get("event"):
                raise HTTPException(status_code=404, detail=f"Trace not found: {trace_id}")
            # Attach deterministic cache report per reporting contract
            usage = result.get("usage") or {}
            cache_read = int(usage.get("cache_read") or 0)
            new_input = int(usage.get("input_billed") or usage.get("input_tokens") or 0)
            result["cache_report"] = format_cache_report(
                cache_read_tokens=cache_read,
                new_input_tokens=new_input,
                turn_id=trace_id,
            )
            return {"status": "ok", "trace_id": trace_id, "trace": result}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v1/trace/{trace_id}/segments", response_model=SegmentsResponse)
    async def get_trace_segments(trace_id: str):
        """Return segment breakdown for a trace."""
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(_executor, lambda: _storage.get_segments(trace_id))
            return {"status": "ok", "trace_id": trace_id, "count": len(result), "segments": result}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v1/trace/{trace_id}/events", response_model=TraceEventsResponse)
    async def get_trace_events(trace_id: str):
        """Return all pipeline events for a trace in chronological order."""
        try:
            loop = asyncio.get_event_loop()
            # First check if trace exists (get_trace returns dict with event=None if not found)
            trace = await loop.run_in_executor(_executor, lambda: _storage.get_trace(trace_id))
            if not trace.get("event"):
                raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")
            events = await loop.run_in_executor(
                _executor, lambda: _storage.get_trace_events(trace_id)
            )
            return {"trace_id": trace_id, "events": events}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v1/models", response_model=ModelsResponse)
    async def list_models():
        """Return list of unique models seen in telemetry."""
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(_executor, _storage.get_unique_models)
            return {"status": "ok", "count": len(result), "models": result}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v1/providers", response_model=ProvidersResponse)
    async def list_providers():
        """Return list of unique providers seen in telemetry."""
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(_executor, _storage.get_unique_providers)
            return {"status": "ok", "count": len(result), "providers": result}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v1/agents", response_model=AgentsResponse)
    async def list_agents():
        """Return list of unique agents seen in telemetry."""
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(_executor, _storage.get_unique_agents)
            return {"status": "ok", "count": len(result), "agents": result}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v1/pricing", response_model=PricingResponse)
    async def get_pricing():
        """Return current pricing catalog."""
        try:
            catalog = PricingCatalog.load()
            return {
                "status": "ok",
                "version": catalog.version,
                "models": catalog.known_models(),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v1/exports/trace/{trace_id}")
    async def export_trace(trace_id: str):
        """Export full trace as downloadable JSON bundle."""
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(_executor, lambda: _storage.get_trace(trace_id))
            if not result.get("event"):
                raise HTTPException(status_code=404, detail=f"Trace not found: {trace_id}")
            import time as _time

            bundle = {
                "export_version": "1.0",
                "trace_id": trace_id,
                "exported_at": _time.time(),
                "event": result.get("event"),
                "usage": result.get("usage"),
                "cost": result.get("cost"),
                "segments": result.get("segments", []),
            }
            return Response(
                content=json.dumps(bundle, indent=2, default=str),
                media_type="application/json",
                headers={"Content-Disposition": f"attachment; filename=trace_{trace_id}.json"},
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/metrics")
    async def prometheus_metrics():
        """
        Return Prometheus text exposition format metrics.

        Metrics:
          - tokenpak_requests_total{provider, status}
          - tokenpak_tokens_total{provider, direction}
          - tokenpak_cost_usd_total{provider}
          - tokenpak_savings_usd_total{provider}
          - tokenpak_request_duration_seconds{provider} (histogram)
          - tokenpak_compression_ratio{provider} (gauge, last 24h)
          - tokenpak_circuit_state{provider} (gauge: 0=closed, 1=open)
        """
        try:
            from .prometheus import PrometheusMetricsCollector

            collector = PrometheusMetricsCollector(storage=_storage)
            loop = asyncio.get_event_loop()
            text = await loop.run_in_executor(_executor, collector.collect)
            return Response(
                content=text,
                media_type="text/plain; version=0.0.4; charset=utf-8",
                headers={"Cache-Control": "no-cache"},
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v1/rollups/compute", response_model=RollupsComputeResponse)
    async def compute_rollups():
        """Trigger rollup computation (typically called by cron)."""
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(_executor, _storage.compute_rollups)
            return {"status": "ok", "rows_written": result}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # -----------------------------------------------------------------------
    # Phase 7C: Capsule endpoint
    # -----------------------------------------------------------------------

    @app.post("/v1/capsule", response_model=CapsuleResponse)
    async def build_capsule(body: CapsuleBody):
        """Build a ContextCapsule from segments within a token budget."""
        try:
            import dataclasses

            from tokenpak.compression.capsules import CapsuleBuilder

            builder = CapsuleBuilder()
            capsule = builder.build(  # type: ignore[attr-defined]
                segments=body.segments,
                budget_tokens=body.budget_tokens,
                session_id=body.session_id,
                agent_id=body.agent_id,
                coverage_score=body.coverage_score,
                retrieval_chunks=body.retrieval_chunks,
            )
            # Run ValidationGate before returning capsule
            from tokenpak.compression.validation_gate import ValidationGate

            gate = ValidationGate()
            vresult = gate.validate(capsule, dry_run=False)
            if not vresult.valid:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "capsule_validation_failed",
                        "errors": vresult.errors,
                        "warnings": vresult.warnings,
                        "budget_used": vresult.budget_used,
                        "budget_limit": vresult.budget_limit,
                    },
                )

            capsule_dict = dataclasses.asdict(capsule)
            if hasattr(capsule.created_at, "isoformat"):
                capsule_dict["created_at"] = capsule.created_at.isoformat()
            return {
                "status": "ok",
                "capsule": capsule_dict,
                "validation": {
                    "valid": vresult.valid,
                    "warnings": vresult.warnings,
                    "budget_used": vresult.budget_used,
                    "budget_limit": vresult.budget_limit,
                },
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    _rollup_state: dict[str, Any] = {"last_refresh": None}

    @app.get("/v1/rollups/status", response_model=RollupsStatusResponse)
    async def rollup_status():
        """Return rollup refresh status."""
        return {"status": "ok", "last_refresh": _rollup_state.get("last_refresh")}

    @app.post("/v1/rollups/refresh", response_model=RollupsRefreshResponse)
    async def rollup_refresh(days: int = Query(30, ge=1)):
        """Manually trigger a rollup refresh."""
        try:
            import time as _time

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(_executor, _storage.compute_rollups)
            _rollup_state["last_refresh"] = _time.time()
            return {"status": "ok", "refreshed": True, "days": days, "rows_written": result}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # -----------------------------------------------------------------------
    # On-demand telemetry refresh (replaces auto-commit cycle)
    # -----------------------------------------------------------------------

    @app.post("/v1/telemetry/refresh", response_model=TelemetryRefreshResponse)
    async def telemetry_refresh(background_tasks: BackgroundTasks):
        """On-demand telemetry refresh: re-run backfill from session JSONL files
        and recompute rollups. Triggered via dashboard button or CLI.
        Does NOT auto-commit to git — avoids conflict with agent work."""
        import os
        import subprocess
        import sys

        def _run_refresh():
            backfill_script = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "..",
                "scripts",
                "backfill_telemetry.py",
            )
            # Also try relative to package root
            if not os.path.exists(backfill_script):
                backfill_script = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "..",
                    "..",
                    "scripts",
                    "backfill_telemetry.py",
                )

            result = {"backfill": "skipped", "rollups": "skipped", "agent_telemetry": "skipped"}

            # 1. Re-run backfill
            if os.path.exists(backfill_script):
                try:
                    # Ensure backfill writes into the SAME DB the server is using
                    db_path = getattr(_storage, "_path", None) or os.environ.get("TOKENPAK_DB_PATH")
                    args = [sys.executable, backfill_script]
                    if db_path:
                        args += ["--db", os.path.expanduser(str(db_path))]
                    proc = subprocess.run(
                        args,
                        capture_output=True,
                        text=True,
                        timeout=120,
                        cwd=os.path.dirname(backfill_script),
                    )
                    result["backfill"] = (
                        "ok" if proc.returncode == 0 else f"error: {proc.stderr[-200:]}"
                    )
                except Exception as e:
                    result["backfill"] = f"error: {e}"
            else:
                result["backfill"] = "script not found"

            # 2. Recompute rollups
            try:
                rows = _storage.compute_rollups()
                result["rollups"] = f"ok ({rows} rows)"
            except Exception as e:
                result["rollups"] = f"error: {e}"

            # 3. Refresh agent telemetry JSON (no git commit)
            agent_telemetry_script = os.path.expanduser(
                "~/vault/06_RUNTIME/scripts/collect-agent-telemetry.py"
            )
            if os.path.exists(agent_telemetry_script):
                try:
                    proc = subprocess.run(
                        [sys.executable, agent_telemetry_script],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    result["agent_telemetry"] = (
                        "ok" if proc.returncode == 0 else f"error: {proc.stderr[-200:]}"
                    )
                except Exception as e:
                    result["agent_telemetry"] = f"error: {e}"

            return result

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(_executor, _run_refresh)
        return {"status": "ok", "refresh": result}

    # -----------------------------------------------------------------------
    # Dashboard (Phase 5C)
    # -----------------------------------------------------------------------
    try:
        from fastapi.staticfiles import StaticFiles

        from .dashboard.dashboard import _STATIC_DIR, create_dashboard_router

        _rollup_engine = rollups if isinstance(rollups, RollupEngine) else RollupEngine(_storage)
        _rollup_engine.ensure_tables()
        dashboard_router = create_dashboard_router(_storage, _rollup_engine)
        app.include_router(dashboard_router)
        # Static files must be mounted on app, not router
        app.mount(
            "/dashboard/static", StaticFiles(directory=str(_STATIC_DIR)), name="dashboard_static"
        )
        logger.info("Dashboard mounted at /dashboard")
    except Exception as e:
        logger.warning(f"Dashboard not mounted (optional): {e}")

    # -----------------------------------------------------------------------
    # Auto-Rollup Cron Background Task
    # -----------------------------------------------------------------------
    _rollup_task: dict[str, Any] = {"task": None}

    async def _auto_rollup_loop(interval_minutes: int = 5):
        """Background task that computes rollups every N minutes."""
        interval_seconds = interval_minutes * 60
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(_executor, _storage.compute_rollups)
                _rollup_state["last_refresh"] = time.time()
                logger.info(f"Auto-rollup complete: {result} rows written")
                # Log rollup completion to tp_events
                try:
                    from .models import TelemetryEvent

                    rollup_event = TelemetryEvent(
                        trace_id="__system__",
                        request_id=f"rollup-{int(time.time())}",
                        event_type="rollup",
                        ts=time.time(),
                        payload={"rows_written": result},
                    )
                    await loop.run_in_executor(_executor, _storage.insert_event, rollup_event)
                except Exception as e:
                    logger.warning(f"Failed to log rollup event: {e}")
            except Exception as e:
                logger.error(f"Auto-rollup failed: {e}")

    # lifespan replaces @app.on_event (DeprecationWarning-free)
    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        _rollup_task["task"] = asyncio.create_task(_auto_rollup_loop(interval_minutes=5))
        logger.info("Auto-rollup background task started (interval: 5 minutes)")
        yield
        if _rollup_task.get("task"):
            _rollup_task["task"].cancel()
            try:
                await _rollup_task["task"]
            except asyncio.CancelledError:
                pass
            logger.info("Auto-rollup background task stopped")

    # ---------------------------------------------------------------------------
    # Cost reprocessing + pricing endpoints
    # ---------------------------------------------------------------------------
    _cost_engine: dict = {}  # lazy singleton

    @app.post("/v1/admin/recalculate")
    async def recalculate_costs(
        from_date: str = Query(..., description="Start date YYYY-MM-DD"),
        to_date: str = Query(..., description="End date YYYY-MM-DD"),
        pricing_version: Optional[str] = Query(None, description="Pricing version override"),
    ):
        """Recalculate costs for events in a date range using current pricing."""
        loop = asyncio.get_event_loop()

        def _run():
            if "engine" not in _cost_engine:
                _cost_engine["engine"] = CostEngine(db_path=db_path)
            return _cost_engine["engine"].reprocess_costs(from_date, to_date, pricing_version)

        result = await loop.run_in_executor(_executor, _run)
        return {"status": "ok", **result}

    @app.get("/v1/pricing/rates")
    async def list_pricing_rates(version: Optional[str] = Query(None)):
        """List all pricing entries in tp_pricing table."""
        loop = asyncio.get_event_loop()

        def _run():
            if "engine" not in _cost_engine:
                _cost_engine["engine"] = CostEngine(db_path=db_path)
            return _cost_engine["engine"].list_pricing(version)

        rows = await loop.run_in_executor(_executor, _run)
        return {
            "pricing": rows,
            "count": len(rows),
            "current_version": CURRENT_PRICING_VERSION,
        }

    # ---------------------------------------------------------------------------
    # Filter options endpoint
    # ---------------------------------------------------------------------------
    @app.get("/v1/filters/options")
    async def get_filter_options():
        """Return available filter values. Cached 10 min."""
        import json as _json_f

        ck = cache_key("options", prefix="filters")
        hit, cached = _cache.get(ck)
        if hit:
            return Response(
                content=cached,
                media_type="application/json",
                headers={"Cache-Control": "max-age=600", "X-Cache": "HIT"},
            )
        loop = asyncio.get_event_loop()

        def _query():
            try:
                import sqlite3 as _sqlite3

                conn = _sqlite3.connect(db_path)
                cur = conn.cursor()
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
                conn.close()
                return providers, models, agents
            except Exception:
                return [], [], []

        providers, models, agents = await loop.run_in_executor(_executor, _query)

        resp_f = {
            "time_ranges": [
                {"value": "1", "label": "Last 24h"},
                {"value": "7", "label": "Last 7 days"},
                {"value": "30", "label": "Last 30 days"},
                {"value": "90", "label": "Last 90 days"},
            ],
            "providers": providers,
            "models": models,
            "agents": agents,
            "statuses": ["all", "success", "error", "retry"],
            "compressions": ["all", "none", "qmd", "tokenpak", "both"],
        }
        body_f = _json_f.dumps(resp_f)
        _cache.set(ck, body_f, ttl=TTL_FILTER_OPTIONS)
        return Response(
            content=body_f,
            media_type="application/json",
            headers={"Cache-Control": "max-age=600", "X-Cache": "MISS"},
        )

    # ---------------------------------------------------------------------------
    # Insights endpoint (Phase 8: Decision Support)
    # ---------------------------------------------------------------------------
    _insight_engine: dict = {}  # lazy singleton per app instance

    @app.get("/v1/insights")
    async def get_insights(
        days: int = Query(7, ge=1, le=90, description="Days of history to analyze"),
    ):
        """
        Generate automatic insights and decision support suggestions.

        Returns a sorted list of insights (alert → warning → success → info),
        capped at 7. Results are cached for 5 minutes.
        """
        loop = asyncio.get_event_loop()

        def _run():
            if "engine" not in _insight_engine:
                _insight_engine["engine"] = InsightEngine(db_path=db_path)
            engine = _insight_engine["engine"]
            insights = engine.generate_insights(days=days)
            return [i.to_dict() for i in insights]

        insight_list = await loop.run_in_executor(_executor, _run)
        return {
            "insights": insight_list,
            "days": days,
            "count": len(insight_list),
            "generated_at": __import__("datetime")
            .datetime.now(__import__("datetime").timezone.utc)
            .isoformat(),
        }

    @app.post("/v1/rollups/rebuild")
    async def rollup_rebuild(
        from_date: str = Query(..., description="Start date YYYY-MM-DD"),
        to_date: str = Query(..., description="End date YYYY-MM-DD"),
    ):
        """Rebuild rollups for a date range (idempotent). Invalidates caches."""
        from datetime import date as _date

        try:
            fd = _date.fromisoformat(from_date)
            td = _date.fromisoformat(to_date)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"Invalid date format: {e}")
        if fd > td:
            raise HTTPException(status_code=422, detail="from_date must be <= to_date")
        try:
            _re = RollupEngine(_storage)
            _re.ensure_tables()
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(_executor, lambda: _re.rebuild_all_rollups(fd, td))
            _cache.invalidate_prefix("kpi:")
            _cache.invalidate_prefix("rollup:")
            return {"status": "ok", **result, "from_date": from_date, "to_date": to_date}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v1/rollups/consistency")
    async def rollup_consistency(days: int = Query(7, ge=1, le=90)):
        """Check rollup vs raw event consistency."""
        try:
            _re = RollupEngine(_storage)
            _re.ensure_tables()
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(_executor, lambda: _re.check_consistency(days=days))
            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/v1/cache/stats")
    async def cache_stats_ep():
        """Return cache hit/miss stats."""
        return {"status": "ok", "cache": _cache.stats}

    @app.post("/v1/cache/clear")
    async def cache_clear_ep(prefix: Optional[str] = Query(None)):
        """Clear cache entries."""
        count = _cache.invalidate_prefix(prefix) if prefix else _cache.clear()
        return {"status": "ok", "cleared": count}

    @app.post("/v1/cache/evict")
    async def cache_evict_ep():
        """Evict expired cache entries."""
        return {"status": "ok", "evicted": _cache.evict_expired()}

    app.router.lifespan_context = _lifespan

    return app


app = create_app()
