"""tokenpak/monitoring/audit_trail.py

Audit trail for TokenPak proxy decisions.

Records *why* tokens were removed or compacted, which blocks were dropped,
what compression method was used, and per-stage timing — enabling root-cause
analysis in production.

Audit events are written to the same async queue as request logs (via the
RequestLogger singleton), with ``level="debug"`` so they only appear when
the logging level is set to "debug".

Usage::

    from tokenpak.monitoring.audit_trail import AuditTrail

    trail = AuditTrail(request_id="abc-123")
    trail.record_compile(
        input_block_count=12,
        output_block_count=7,
        blocks_removed=[{"id": "kb-1", "reason": "low_relevance"}],
        compression_method="extractive",
        stage_timings={"parse": 4.2, "compile": 88.1, "render": 3.0},
        input_block_types={"instructions": 2, "knowledge": 5, "evidence": 5},
        output_block_types={"instructions": 2, "knowledge": 3, "evidence": 2},
    )
    trail.record_cache(
        operation="get",
        block_id="kb-1",
        hit=True,
        cached_size=2048,
    )
    trail.flush()   # enqueues all recorded events to the logger
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .request_logger import RequestLogger, RequestLogRecord, LEVEL_DEBUG


class AuditTrail:
    """
    Collects audit events for a single request and flushes them to the
    RequestLogger in one batch.

    Parameters
    ----------
    request_id : str
        Shared with the corresponding RequestLogRecord for correlation.
    """

    def __init__(self, request_id: str) -> None:
        self._request_id = request_id
        self._events: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Recording methods
    # ------------------------------------------------------------------

    def record_compile(
        self,
        *,
        input_block_count: int = 0,
        output_block_count: int = 0,
        blocks_removed: Optional[List[Dict[str, Any]]] = None,
        compression_method: str = "",
        stage_timings: Optional[Dict[str, float]] = None,
        input_block_types: Optional[Dict[str, int]] = None,
        output_block_types: Optional[Dict[str, int]] = None,
        tokens_before: int = 0,
        tokens_after: int = 0,
    ) -> None:
        """Record a /compile (compression) decision."""
        event: Dict[str, Any] = {
            "event": "compile",
            "input_block_count": input_block_count,
            "output_block_count": output_block_count,
            "blocks_removed_count": len(blocks_removed) if blocks_removed else 0,
            "compression_method": compression_method,
        }
        if blocks_removed:
            event["blocks_removed"] = blocks_removed
        if stage_timings:
            event["stage_timings_ms"] = stage_timings
        if input_block_types:
            event["input_block_types"] = input_block_types
        if output_block_types:
            event["output_block_types"] = output_block_types
        if tokens_before:
            event["tokens_before"] = tokens_before
        if tokens_after:
            event["tokens_after"] = tokens_after
        self._events.append(event)

    def record_cache(
        self,
        *,
        operation: str = "get",
        block_id: str = "",
        hit: Optional[bool] = None,
        cached_size: int = 0,
    ) -> None:
        """Record a /cache/* operation."""
        event: Dict[str, Any] = {
            "event": "cache",
            "operation": operation,
            "block_id": block_id,
        }
        if hit is not None:
            event["cache_hit"] = hit
        if cached_size:
            event["cached_size"] = cached_size
        self._events.append(event)

    def record_metrics(
        self,
        *,
        aggregation_window: str = "",
        data_points_returned: int = 0,
    ) -> None:
        """Record a /metrics aggregation event."""
        self._events.append({
            "event": "metrics",
            "aggregation_window": aggregation_window,
            "data_points_returned": data_points_returned,
        })

    def record_error(self, *, error_type: str, message: str, **extra) -> None:
        """Record an error that occurred during request processing."""
        event: Dict[str, Any] = {
            "event": "error",
            "error_type": error_type,
            "message": message,
        }
        event.update(extra)
        self._events.append(event)

    # ------------------------------------------------------------------
    # Flush
    # ------------------------------------------------------------------

    def flush(self) -> None:
        """Enqueue all recorded events to the RequestLogger."""
        if not self._events:
            return
        logger = RequestLogger.get_instance()
        for event in self._events:
            record = RequestLogRecord(
                request_id=self._request_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                level=LEVEL_DEBUG,
                endpoint="",
                extra=event,
            )
            logger.log(record)
        self._events.clear()

    def __len__(self) -> int:
        return len(self._events)

    def __repr__(self) -> str:
        return f"AuditTrail(request_id={self._request_id!r}, events={len(self._events)})"
