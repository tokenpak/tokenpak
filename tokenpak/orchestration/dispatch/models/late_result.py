"""LateResult record."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .common import DispatchBaseModel


class LateResult(DispatchBaseModel):
    """A TIP result that arrived after cancellation.

    ``effects_applied`` is always ``False`` in v0.1-alpha (late effects are
    never applied); ``recovery_allowed`` gates the inspect-only path
    (recovery itself is deferred to beta).
    """

    id: str = Field(description='"late_<ulid>"')
    job_id: str
    station_run_id: str
    received_at: datetime
    result_hash: str
    stored_artifact_id: str | None = None
    effects_applied: bool = Field(default=False, description="always false in v0.1-alpha")
    recovery_allowed: bool = Field(
        default=False, description="v0.1-alpha: inspect-only; recovery deferred"
    )


__all__ = ["LateResult"]
