"""Backwards-compatible re-export shim.

The canonical home for Prometheus metrics collection is
``tokenpak.telemetry.metrics`` (Architecture §1, §7.1). This module
re-exports from the canonical location so legacy callers keep working.
Deprecated since 2026-04-20; target removal in TIP-2.0.

The D1 migration (Architecture §10 debt item D1) moved this module;
the shim is a one-MINOR bridge. Direct imports of
``tokenpak.telemetry.metrics`` are preferred.
"""

from __future__ import annotations

import warnings

from tokenpak.telemetry.metrics.collector import (  # noqa: F401
    ProxyMetricsCollector,
)

warnings.warn(
    "tokenpak.monitoring.metrics is deprecated — "
    "import from tokenpak.telemetry.metrics (canonical home since 2026-04-20, D1 migration). "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["ProxyMetricsCollector"]
