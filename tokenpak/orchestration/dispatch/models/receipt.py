"""DispatchReceipt record (Standards Delta v0 §4.7)."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .common import DispatchBaseModel


class ReceiptStation(DispatchBaseModel):
    """Per-station summary row on a receipt (Standards Delta v0 §4.7)."""

    station_run_id: str
    worker_id: str
    status: str
    tip_request_ids: list[str] = Field(default_factory=list)
    result_payload_excerpt: str = Field(
        default="", description="first 500 chars; full in DispatchStationRun"
    )


class ReceiptDecision(DispatchBaseModel):
    """Per-decision summary row on a receipt (Standards Delta v0 §4.7)."""

    decision_id: str
    status: str


class ReceiptEffect(DispatchBaseModel):
    """Per-effect summary row on a receipt (Standards Delta v0 §4.7)."""

    effect_id: str
    status: str
    target: str


class ReceiptTelemetry(DispatchBaseModel):
    """Aggregated telemetry block on a receipt (Standards Delta v0 §4.7)."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_latency_ms: int = 0
    cache_hits: int = 0
    estimated_cost: float | None = None


class DispatchReceipt(DispatchBaseModel):
    """Delivery receipt summarizing a completed run (Standards Delta v0 §4.7)."""

    id: str = Field(description='"receipt_<ulid>"')
    job_id: str
    run_id: str
    route_id: str

    stations: list[ReceiptStation] = Field(default_factory=list)
    decisions: list[ReceiptDecision] = Field(default_factory=list)
    effects: list[ReceiptEffect] = Field(default_factory=list)

    telemetry: ReceiptTelemetry = Field(default_factory=ReceiptTelemetry)

    final_status: str
    created_at: datetime


__all__ = [
    "ReceiptStation",
    "ReceiptDecision",
    "ReceiptEffect",
    "ReceiptTelemetry",
    "DispatchReceipt",
]
