"""Compression-stage orchestration.

Thin wrapper that drives the ``compression/`` primitive from inside the
services pipeline. Chooses strategy, applies budgets, respects canon
(never-touch) blocks, and records compression-side telemetry.

Phase 2 scaffold. Logic extracts from ``proxy/`` in task P2-02.
"""

from __future__ import annotations
