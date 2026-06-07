"""Trace models for the optimization pipeline.

These are services-layer mirror types: they carry just enough information
for observe-only telemetry. When the trace contract
(``tokenpak.tip.trace_contract``) is available the pipeline can also emit a
contract-shaped trace — see ``trace.to_tip_dict()``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class StageTrace:
    """Per-stage trace entry.

    name:        stage's machine identifier
    eligible:    eligibility verdict (True/False)
    skip_reason: empty when eligible=True; otherwise a short token
    applied:     True only if the stage actually mutated ctx. Observe-only
                 pipelines must always emit applied=False for every stage.
    duration_ms: monotonic-clock duration of the eligibility check (the
                 only thing that ran in observe-only mode)
    detail:      free-form note for debugging
    """

    name: str
    eligible: bool
    skip_reason: str = ""
    applied: bool = False
    duration_ms: float = 0.0
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "eligible": self.eligible,
            "skip_reason": self.skip_reason,
            "applied": self.applied,
            "duration_ms": round(self.duration_ms, 4),
            "detail": self.detail,
        }


@dataclass
class OptimizationTrace:
    """Top-level trace for one request through the pipeline."""

    request_id: str
    mode: str = "observe"
    started_at: float = field(default_factory=time.time)
    stages: List[StageTrace] = field(default_factory=list)
    bypass_reason: str = ""
    body_bytes_in: int = 0
    body_bytes_out: int = 0

    def add_stage(self, st: StageTrace) -> None:
        self.stages.append(st)

    def mark_bypass(self, reason: str) -> None:
        self.bypass_reason = reason

    @property
    def body_unchanged(self) -> bool:
        """True when no stage was applied AND byte counts match."""
        if any(s.applied for s in self.stages):
            return False
        return self.body_bytes_in == self.body_bytes_out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "mode": self.mode,
            "started_at": self.started_at,
            "bypass_reason": self.bypass_reason,
            "body_bytes_in": self.body_bytes_in,
            "body_bytes_out": self.body_bytes_out,
            "body_unchanged": self.body_unchanged,
            "stages": [s.to_dict() for s in self.stages],
        }

    def to_tip_dict(self) -> Dict[str, Any]:
        """Return a dict shaped for the canonical trace contract.

        Falls back to ``to_dict()`` when ``tokenpak.tip`` isn't importable.
        When the trace contract is available, this is the bridge into the
        canonical trace schema; the `tip_version` discriminator confirms the
        shape.
        """
        try:
            from tokenpak.tip.trace_contract import OptimizationTrace as _TipTrace  # noqa: F401
            return {"tip_version": "v1", **self.to_dict()}
        except Exception:
            return self.to_dict()
