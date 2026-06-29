"""DispatchStationRun record."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from .common import DispatchBaseModel
from .enums import StationRunStatus


class DispatchStationRun(DispatchBaseModel):
    """Per-station execution record within a run.

    ``status`` uses the exact 9-member :class:`StationRunStatus` enum required
    by the P-SCHEMA-01 acceptance criteria. ``result_payload`` is the
    schema-valid station output, or ``None`` if the station failed.
    """

    id: str = Field(description='"stationrun_<ulid>"')
    run_id: str
    station_id: str
    worker_id: str
    prompt_overlay_id: str | None = None

    context_bundle_id: str
    tip_request_ids: list[str] = Field(
        default_factory=list, description="may be >1 due to loop iterations"
    )

    status: StationRunStatus

    iteration_count: int = 0
    tool_call_count: int = 0
    wall_seconds: int = 0

    result_payload: dict[str, Any] | None = None
    result_schema_version: str

    attempt_number: int = Field(
        default=1, description="1 for first try; increments on rerun"
    )


__all__ = ["DispatchStationRun"]
