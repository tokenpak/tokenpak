"""tokenpak.metrics.prometheus — compatibility shim. Canonical location: tokenpak.telemetry.metrics.prometheus."""

from tokenpak.telemetry.metrics.prometheus import *  # noqa: F401, F403
from tokenpak.telemetry.metrics.prometheus import (  # noqa: F401
    PrometheusRegistry,
    _counter,
    _escape_label_value,
    _gauge,
    _histogram_lines,
    _labels,
    build_metrics_text,
)
