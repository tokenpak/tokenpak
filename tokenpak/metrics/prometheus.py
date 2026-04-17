"""tokenpak.metrics.prometheus — compatibility shim. Canonical location: tokenpak.telemetry.metrics.prometheus."""
from tokenpak.telemetry.metrics.prometheus import *  # noqa: F401, F403
from tokenpak.telemetry.metrics.prometheus import (  # noqa: F401
    _escape_label_value,
    _labels,
    _counter,
    _gauge,
    _histogram_lines,
    PrometheusRegistry,
    build_metrics_text,
)
