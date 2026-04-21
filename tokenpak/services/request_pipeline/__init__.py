"""Request-lifecycle + pipeline composition.

Composes the canonical services pipeline:

    compression -> security -> cache -> routing -> telemetry -> dispatch

Phase 2 scaffold. Pipeline code extracts from ``proxy/`` in task P2-01.
"""

from __future__ import annotations
