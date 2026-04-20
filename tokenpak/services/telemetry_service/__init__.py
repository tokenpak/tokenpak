"""Telemetry emission (Phase 2 stage wrapper).

Single writer of the telemetry store (Architecture §7.1). Thin-wraps
``tokenpak.telemetry`` (``TelemetryCollector``, ``CanonicalRequest``,
``CanonicalResponse`` are real public types).

P2-05 acceptance: exactly one row per request that exits the
pipeline. Entrypoints never write here — they read via the
telemetry subsystem's public API.
"""

from __future__ import annotations

from ..request_pipeline.stages import PipelineContext


class Stage:
    """Telemetry emission pipeline stage."""

    name = "telemetry"

    def apply_request(self, ctx: PipelineContext) -> None:
        """Record request arrival time for per-request latency attribution.

        Phase 2 pass-through. Real version stamps
        ``ctx.extras['t_request']`` with ``time.perf_counter()``.
        """
        return None

    def apply_response(self, ctx: PipelineContext) -> None:
        """Write one row to the telemetry store on the way out.

        Phase 2 pass-through. Real version constructs a
        ``CanonicalRequest`` + ``CanonicalResponse`` from ctx, calls
        ``TelemetryCollector.record(...)``. The
        ``cache_origin`` field is whatever ``cache_service`` set in
        ctx.extras; if not set, default to ``'unknown'`` per
        Constitution §5.3.
        """
        return None
