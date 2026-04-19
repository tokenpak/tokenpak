"""Pydantic response models for all TokenPak telemetry FastAPI endpoints.

Every ``@app.get`` / ``@app.post`` in server.py uses one of these models
as its ``response_model=`` so that:
  - FastAPI generates accurate OpenAPI/Swagger docs
  - Responses are validated before they leave the server
  - Test clients can rely on a stable contract

Usage::

    from tokenpak.telemetry.response_models import HealthResponse, StatsResponse
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """Health check response with status and optional diagnostics."""

    status: str = "ok"
    service: str = "tokenpak-telemetry"


# ---------------------------------------------------------------------------
# /v1/telemetry/stats
# ---------------------------------------------------------------------------


class StatsResponse(BaseModel):
    """Aggregated statistics response (counts and summaries)."""

    status: str = "ok"
    stats: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# /v1/summary
# ---------------------------------------------------------------------------


class SummaryTotals(BaseModel):
    """Aggregate totals (tokens, costs, etc) for the summary."""

    total_requests: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0
    total_savings: float = 0.0
    period_days: int = 30

    model_config = {"extra": "allow"}


class SummaryData(BaseModel):
    """Summary data including totals and breakdown by dimension."""

    totals: SummaryTotals = Field(default_factory=SummaryTotals)
    by_provider: List[Dict[str, Any]] = Field(default_factory=list)
    by_model: List[Dict[str, Any]] = Field(default_factory=list)
    by_agent: List[Dict[str, Any]] = Field(default_factory=list)
    period_days: int = 30


class SummaryResponse(BaseModel):
    """Cost summary response with aggregated totals and trends."""

    status: str = "ok"
    summary: SummaryData = Field(default_factory=SummaryData)


# ---------------------------------------------------------------------------
# /v1/timeseries
# ---------------------------------------------------------------------------


class TimeseriesResponse(BaseModel):
    """Timeseries data (daily or hourly aggregates)."""

    status: str = "ok"
    metric: str
    interval: str
    data: List[Dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# /v1/traces
# ---------------------------------------------------------------------------


class TracesResponse(BaseModel):
    """Paginated list of trace records."""

    status: str = "ok"
    limit: int
    offset: int
    count: int
    has_more: bool = False
    next_offset: Optional[int] = None
    traces: List[Dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# /v1/trace/{trace_id}
# ---------------------------------------------------------------------------


class TraceDetailResponse(BaseModel):
    """Detailed trace record with full context."""

    status: str = "ok"
    trace_id: str
    trace: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# /v1/trace/{trace_id}/segments
# ---------------------------------------------------------------------------


class SegmentsResponse(BaseModel):
    """Paginated list of segment records."""

    status: str = "ok"
    trace_id: str
    count: int
    segments: List[Dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# /v1/trace/{trace_id}/events
# ---------------------------------------------------------------------------


class TraceEventsResponse(BaseModel):
    """List of trace events."""

    trace_id: str
    events: List[Dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# /v1/models  /v1/providers  /v1/agents
# ---------------------------------------------------------------------------


class ModelsResponse(BaseModel):
    """List of LLM models used."""

    status: str = "ok"
    count: int
    models: List[str] = Field(default_factory=list)


class ProvidersResponse(BaseModel):
    """List of LLM providers used."""

    status: str = "ok"
    count: int
    providers: List[str] = Field(default_factory=list)


class AgentsResponse(BaseModel):
    """List of agents by name."""

    status: str = "ok"
    count: int
    agents: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# /v1/pricing
# ---------------------------------------------------------------------------


class PricingResponse(BaseModel):
    """Per-model pricing information."""

    status: str = "ok"
    version: str = ""
    models: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# /v1/rollups/compute
# ---------------------------------------------------------------------------


class RollupsComputeResponse(BaseModel):
    """Status/result of a compute rollup operation."""

    status: str = "ok"
    rows_written: Union[int, Dict[str, Any]] = 0


# ---------------------------------------------------------------------------
# /v1/capsule
# ---------------------------------------------------------------------------


class CapsuleResponse(BaseModel):
    """Capsule (snapshot) response."""

    status: str = "ok"
    capsule: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# /v1/rollups/status
# ---------------------------------------------------------------------------


class RollupsStatusResponse(BaseModel):
    """Status of rollup operations."""

    status: str = "ok"
    last_refresh: Optional[float] = None


# ---------------------------------------------------------------------------
# /v1/rollups/refresh
# ---------------------------------------------------------------------------


class RollupsRefreshResponse(BaseModel):
    """Result of refreshing rollup tables."""

    status: str = "ok"
    refreshed: bool = True
    days: int = 30
    rows_written: Union[int, Dict[str, Any]] = 0


# ---------------------------------------------------------------------------
# /v1/telemetry/refresh
# ---------------------------------------------------------------------------


class TelemetryRefreshDetail(BaseModel):
    """Detail of a single refresh operation."""

    backfill: str = "skipped"
    rollups: str = "skipped"
    agent_telemetry: str = "skipped"

    model_config = {"extra": "allow"}


class TelemetryRefreshResponse(BaseModel):
    """Response summarizing telemetry refresh results."""

    status: str = "ok"
    refresh: TelemetryRefreshDetail = Field(default_factory=TelemetryRefreshDetail)
