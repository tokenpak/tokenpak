"""Routing decisions + fallback execution.

Pipeline-side driver for the ``routing/`` primitive. Selects provider/
model per routing rules, executes fallback chains on provider failure,
respects circuit-breaker state.

Phase 2 scaffold. Logic extracts from ``proxy/`` in task P2-04.
"""

from __future__ import annotations
