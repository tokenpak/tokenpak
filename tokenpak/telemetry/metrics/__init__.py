"""Telemetry metrics — Prometheus exposition + aggregated counters.

Architecture §1 + §7.1: telemetry subsystem's metrics package. Provides
the Prometheus text-exposition layer that reads from the telemetry
store and the proxy's in-memory counters.

Canonical home as of the D1 partial-consolidation pass (2026-04-20).
Previously at ``tokenpak/monitoring/metrics.py``; the old import path
is kept working as a re-export shim for one MINOR (Constitution §5.6
no-versioned-filenames + backwards-compat convention).

Public surface: ``ProxyMetricsCollector``.
"""

from __future__ import annotations

from .collector import ProxyMetricsCollector  # noqa: F401

__all__ = ["ProxyMetricsCollector"]
