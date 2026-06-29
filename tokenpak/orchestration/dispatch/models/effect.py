"""DispatchEffect record."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .common import DispatchBaseModel
from .enums import EffectStatus, EffectTargetType, RollbackBehavior


class DispatchEffect(DispatchBaseModel):
    """A single workspace-mutating effect record.

    Covers the three file-state cases:

    * **create** — ``before_exists=False``, ``before_hash=None``,
      ``after_hash`` set, ``rollback_behavior=delete_file_if_after_hash_matches``.
    * **modify** — ``before_exists=True`` with both ``before_hash`` and
      ``after_hash`` set,
      ``rollback_behavior=restore_before_content_if_current_hash_matches_after_hash``.
    * **delete** — ``before_exists=True``, ``before_hash`` set,
      ``after_hash=None``, ``rollback_behavior=restore_before_content``.

    Effect-record protocol: every effect-bearing tool call creates a
    ``planned`` record BEFORE execution and transitions to ``applied`` AFTER
    success. A ``planned`` record without ``finalized_at`` signals an
    interrupted effect for resume reconciliation.
    """

    id: str = Field(description='"effect_<ulid>"')
    job_id: str
    station_run_id: str
    tool_name: str = Field(description='e.g. "apply_patch"')

    target_type: EffectTargetType
    target: str = Field(description="path or identifier")

    before_exists: bool
    before_hash: str | None = None
    after_hash: str | None = None

    rollback_behavior: RollbackBehavior
    status: EffectStatus

    rollback_available: bool = False
    created_at: datetime
    finalized_at: datetime | None = None


__all__ = ["DispatchEffect"]
