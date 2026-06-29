"""DispatchRun record."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .common import DispatchBaseModel


class DispatchRun(DispatchBaseModel):
    """Top-level job run record.

    ``status`` is typed ``str`` (it tracks
    ``DispatchJob.status``; not re-typed to the enum to match the spec verbatim).
    The ``*_runs`` / ``decisions`` / ``effects`` / ``late_results`` lists hold
    the string ids of the related records.
    """

    id: str = Field(description='"run_<ulid>"')
    job_id: str
    manifest_id: str
    route_id: str
    started_at: datetime
    ended_at: datetime | None = None
    status: str = Field(description="tracks DispatchJob.status")
    station_runs: list[str] = Field(
        default_factory=list, description="DispatchStationRun.id values"
    )
    decisions: list[str] = Field(
        default_factory=list, description="DispatchDecision.id values"
    )
    effects: list[str] = Field(
        default_factory=list, description="DispatchEffect.id values"
    )
    late_results: list[str] = Field(
        default_factory=list, description="LateResult.id values"
    )
    receipt_id: str | None = None


__all__ = ["DispatchRun"]
