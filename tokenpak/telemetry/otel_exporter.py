"""tokenpak.telemetry.otel_exporter — OpenTelemetry span + metrics exporter.

Enabled only when TOKENPAK_OTEL_ENDPOINT is set to a non-empty string.
Gracefully degrades when the opentelemetry SDK is not installed.
All public functions are safe to call regardless of SDK availability.
"""
from __future__ import annotations

import os
from typing import Any, Optional

# ── Module-level state ───────────────────────────────────────────────────────

_ENDPOINT: str = os.environ.get("TOKENPAK_OTEL_ENDPOINT", "").strip()
_ENABLED: bool = bool(_ENDPOINT)

_tracer: Any = None
_meter: Any = None
_counter_requests: Any = None
_counter_cache: Any = None
_histogram_duration: Any = None
_histogram_compression: Any = None

_initialized: bool = False


# ── Public API ───────────────────────────────────────────────────────────────

def is_enabled() -> bool:
    """Return True when TOKENPAK_OTEL_ENDPOINT is set to a non-empty string."""
    return _ENABLED


def _init() -> None:
    """Initialise OTel tracer and meter. Disables the exporter on failure."""
    global _tracer, _meter, _counter_requests, _counter_cache
    global _histogram_duration, _histogram_compression, _initialized, _ENABLED

    if _initialized:
        return

    _initialized = True

    try:
        from opentelemetry import trace as otel_trace, metrics as otel_metrics
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

        tracer_provider = TracerProvider()
        span_exporter = OTLPSpanExporter(endpoint=_ENDPOINT + "/v1/traces")
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        otel_trace.set_tracer_provider(tracer_provider)
        _tracer = otel_trace.get_tracer("tokenpak")

        metric_exporter = OTLPMetricExporter(endpoint=_ENDPOINT + "/v1/metrics")
        reader = PeriodicExportingMetricReader(metric_exporter)
        meter_provider = MeterProvider(metric_readers=[reader])
        otel_metrics.set_meter_provider(meter_provider)
        _meter = otel_metrics.get_meter("tokenpak")

        _counter_requests = _meter.create_counter(
            "tokenpak.requests",
            description="Total proxy requests",
        )
        _counter_cache = _meter.create_counter(
            "tokenpak.cache",
            description="Cache hit/miss counts",
        )
        _histogram_duration = _meter.create_histogram(
            "tokenpak.duration_ms",
            description="Request duration in milliseconds",
        )
        _histogram_compression = _meter.create_histogram(
            "tokenpak.compression_ratio",
            description="Token compression ratio",
        )
    except Exception:
        _ENABLED = False
        _tracer = None


def record_request(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    compression_ratio: float,
    cache_hit: bool,
    status_code: int,
    duration_ms: float,
) -> None:
    """Record a proxy request as an OTel span + metric increments.

    No-op when the exporter is disabled or the SDK is unavailable.
    Never raises.
    """
    if not _ENABLED:
        return

    try:
        _init()
    except Exception:
        return

    if _tracer is None:
        return

    try:
        attrs = {
            "tokenpak.model": model,
            "tokenpak.input_tokens": input_tokens,
            "tokenpak.output_tokens": output_tokens,
            "tokenpak.status_code": status_code,
            "tokenpak.duration_ms": duration_ms,
            "tokenpak.cache_hit": cache_hit,
        }

        with _tracer.start_as_current_span("tokenpak.proxy_request") as span:
            for k, v in attrs.items():
                span.set_attribute(k, v)

            if status_code >= 500:
                try:
                    from opentelemetry.trace import StatusCode, Status
                    span.set_status(Status(StatusCode.ERROR))
                except Exception:
                    pass

        cache_result = "hit" if cache_hit else "miss"

        if _counter_requests is not None:
            _counter_requests.add(1, {"model": model, "status": str(status_code)})

        if _counter_cache is not None:
            _counter_cache.add(1, {"result": cache_result, "model": model})

        if _histogram_duration is not None:
            _histogram_duration.record(duration_ms, {"model": model})

        if _histogram_compression is not None:
            _histogram_compression.record(compression_ratio, {"model": model})

    except Exception:
        pass
