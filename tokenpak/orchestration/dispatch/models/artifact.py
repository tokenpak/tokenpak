"""DispatchArtifact record (Standards Delta v0 §2/§4 record list — SKETCH).

The Standards Delta enumerates ``DispatchArtifact`` in the §2 record vocabulary
and §4 record list but does NOT provide a full field schema for it (the §4
note for the record list reads "sketch needed"). This module is therefore a
faithful **sketch**, not a transcription: fields follow the artifact semantics
established elsewhere in the delta — the ``write_artifact`` tool writes to
``~/.tpk/dispatch/artifacts/`` (§5.3) and §5.7 passes ``artifacts`` into the
Reviewer Station. A later packet may expand this once the artifact contract is
fully specified; the field set here is intentionally minimal and additive-safe.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from .common import DispatchBaseModel


class DispatchArtifact(DispatchBaseModel):
    """A stored Dispatch artifact in the Run Ledger (SKETCH — see module docstring)."""

    id: str = Field(description='"artifact_<ulid>"')
    job_id: str
    station_run_id: str | None = None

    kind: str = Field(description='e.g. "patch", "doc", "report"')
    target: str = Field(
        description="storage location under ~/.tpk/dispatch/artifacts/"
    )
    content_hash: str
    size_bytes: int | None = None

    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = ["DispatchArtifact"]
