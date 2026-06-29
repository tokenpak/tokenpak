"""DispatchJob record."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .common import DispatchBaseModel
from .enums import AutonomyMode, DispatchJobStatus


class DispatchJob(DispatchBaseModel):
    """Top-level intake record for a dispatched request.

    ``status`` is the Dispatch execution-tier state machine, independent of
    the task-packet enum. ``source_task_packet_id`` is the task-packet
    crosswalk hook: ``None`` means the job is standalone (not task-packet-linked).
    """

    id: str = Field(description='"job_<ulid>"')
    created_at: datetime
    raw_request: str
    source_task_packet_id: str | None = Field(
        default=None,
        description="task-packet crosswalk hook; null = standalone job.",
    )

    detected_intent: str = Field(
        description='registry-bound; e.g. "code_task", "doc_task", "quick_answer", "unknown"'
    )
    route_hint: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(
        default_factory=list,
        description="registry-bound (PAKPlan risk_flag registry)",
    )

    autonomy_mode: AutonomyMode
    status: DispatchJobStatus


__all__ = ["DispatchJob"]
