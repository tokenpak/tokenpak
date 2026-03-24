"""
TokenPak OpenTelemetry Exporter

Exports request spans and metrics to an OTLP-compatible backend when
TOKENPAK_OTEL_ENDPOINT is set.  When the env var is absent the module
is a complete no-op: zero imports from the opentelemetry package and
zero runtime overhead.

Usage
-----
Set the env var before starting the proxy::

    TOKENPAK_OTEL_ENDPOINT=http://localhost:4317 tokenpak ...

or (HTTP/JSON endpoint)::

    TOKENPAK_OTEL_ENDPOINT=http://localhost:4318/v1/traces ...

Spans
-----
One span per proxied request with attributes:

    tokenpak.model              string
    tokenpak.input_tokens       int   (raw, before compression)
    tokenpak.output_tokens      int
    tokenpak.compression_ratio  float (sent / raw; 1.0 when no compression)
    tokenpak.cache_hit          bool
    http.status_code            int

Metrics (counter / histogram)
------------------------------
    tokenpak.requests.total          counter
    tokenpak.tokens.input            counter
    tokenpak.tokens.output           counter
    tokenpak.compression.ratio       histogram
    tokenpak.cache.hit_rate          counter  (labels: hit|miss)
"""

from __future__ import annotations

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feature flag — read once at import time
# ---------------------------------------------------------------------------
OTEL_ENDPOINT: Optional[str] = os.environ.get("TOKENPAK_OTEL_ENDPOINT", "").strip() or None
_ENABLED = bool(OTEL_ENDPOINT)

# ---------------------------------------------------------------------------
# Lazy OTel initialisation — only if enabled
# ---------------------------------------------------------------------------
_tracer = None
_meter = None

# metric instruments (populated in _init)
_counter_requests = None
_counter_tokens_input = None
_counter_tokens_output = None
_histogram_compression = None
_counter_cache = None


def _init() -> None:
    """Initialise OTel SDK.  Called once lazily on first use."""
    global _tracer, _meter
    global _counter_requests, _counter_tokens_input, _counter_tokens_output
    global _histogram_compression, _counter_cache

    if _tracer is not None:
        return  # already initialised

    try:
        from opentelemetry import trace, metrics
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        # ------------------------------------------------------------------
        # Detect transport from endpoint URL
        # ------------------------------------------------------------------
        endpoint = OTEL_ENDPOINT or ""

        if "4317" in endpoint or endpoint.startswith("grpc://"):
            # gRPC OTLP
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter as _TExp
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter as _MExp
            span_exporter = _TExp(endpoint=endpoint)
            metric_exporter = _MExp(endpoint=endpoint)
        else:
            # HTTP/protobuf OTLP (default)
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter as _TExp
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter as _MExp
            span_exporter = _TExp(endpoint=endpoint)
            metric_exporter = _MExp(endpoint=endpoint)

        # Tracing
        tracer_provider = TracerProvider()
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        trace.set_tracer_provider(tracer_provider)
        _tracer = trace.get_tracer("tokenpak", schema_url="https://opentelemetry.io/schemas/1.24.0")

        # Metrics
        reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=60_000)
        meter_provider = MeterProvider(metric_readers=[reader])
        metrics.set_meter_provider(meter_provider)
        _meter = metrics.get_meter("tokenpak")

        # Instruments
        _counter_requests = _meter.create_counter(
            "tokenpak.requests.total",
            description="Total proxied requests",
        )
        _counter_tokens_input = _meter.create_counter(
            "tokenpak.tokens.input",
            description="Total raw input tokens",
        )
        _counter_tokens_output = _meter.create_counter(
            "tokenpak.tokens.output",
            description="Total output tokens",
        )
        _histogram_compression = _meter.create_histogram(
            "tokenpak.compression.ratio",
            description="Compression ratio (sent/raw; 1.0 = uncompressed)",
            unit="ratio",
        )
        _counter_cache = _meter.create_counter(
            "tokenpak.cache.hit_rate",
            description="Cache hit or miss counts (attribute: result=hit|miss)",
        )

        logger.info("TokenPak OTel exporter initialised → %s", endpoint)

    except ImportError as exc:
        logger.warning(
            "TOKENPAK_OTEL_ENDPOINT set but opentelemetry packages not installed "
            "(%s). Install with: pip install tokenpak[otel]",
            exc,
        )
        # Disable silently so proxy continues without OTel
        global _ENABLED
        _ENABLED = False
    except Exception as exc:  # pragma: no cover — endpoint unreachable
        logger.warning("OTel initialisation failed (%s). Continuing without OTel.", exc)
        _ENABLED = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

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
    """Record a completed proxy request.

    When OTel is disabled this function returns immediately without any
    work.  All exceptions are swallowed so this can never affect the
    proxy request path.
    """
    if not _ENABLED:
        return

    try:
        _init()
        if not _ENABLED:  # init may have disabled
            return

        attrs = {"model": model}
        _counter_requests.add(1, attributes=attrs)
        _counter_tokens_input.add(input_tokens, attributes=attrs)
        _counter_tokens_output.add(output_tokens, attributes=attrs)
        _histogram_compression.record(compression_ratio, attributes=attrs)
        _counter_cache.add(1, attributes={"result": "hit" if cache_hit else "miss"})

        # Span
        from opentelemetry import trace as _trace
        tracer = _trace.get_tracer("tokenpak")
        with tracer.start_as_current_span("tokenpak.proxy_request") as span:
            span.set_attribute("tokenpak.model", model)
            span.set_attribute("tokenpak.input_tokens", input_tokens)
            span.set_attribute("tokenpak.output_tokens", output_tokens)
            span.set_attribute("tokenpak.compression_ratio", round(compression_ratio, 4))
            span.set_attribute("tokenpak.cache_hit", cache_hit)
            span.set_attribute("http.status_code", status_code)
            span.set_attribute("tokenpak.duration_ms", round(duration_ms, 2))

            if status_code >= 400:
                span.set_status(
                    _trace.Status(_trace.StatusCode.ERROR, f"HTTP {status_code}")
                )

    except Exception as exc:
        logger.debug("OTel record_request failed (non-fatal): %s", exc)


def is_enabled() -> bool:
    """Return True if OTel export is configured and active."""
    return _ENABLED
