"""tokenpak.telemetry.export — DEPRECATED stub.

The TelemetryExporter class was never implemented.  Actual export
functionality lives in:

  - tokenpak.dashboard.export_csv.CSVExporter  (CSV export)
  - tokenpak.telemetry.cost.CostEngine          (cost queries)

This module is kept only to avoid breaking any external code that
may reference MAX_EXPORT_ROWS.  Remove after 2026-07-15.
"""

from __future__ import annotations

import warnings

MAX_EXPORT_ROWS = 10_000

__all__ = ["MAX_EXPORT_ROWS"]


def __getattr__(name: str):
    if name == "TelemetryExporter":
        warnings.warn(
            "TelemetryExporter is deprecated and was never implemented. "
            "Use tokenpak.dashboard.export_csv.CSVExporter instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        raise ImportError("TelemetryExporter was removed; use CSVExporter")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
