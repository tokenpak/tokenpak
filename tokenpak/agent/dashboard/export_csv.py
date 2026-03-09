"""tokenpak.agent.dashboard.export_csv — CSV export for dashboard data.

Generates downloadable CSV files from proxy pipeline data.
Supports:
  - traces: full pipeline trace records (all columns or summary)
  - stats: current session statistics as key-value pairs

Design goals:
  - No BOM, no extra whitespace, properly RFC-4180 escaped
  - Pure stdlib (csv + io + datetime) — zero extra deps
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from _csv import writer as CsvWriter
else:
    CsvWriter = Any  # type: ignore


class ExportFormat(str, Enum):
    FULL = "full"  # All columns
    SIMPLIFIED = "simplified"  # Summary / high-value columns only


class ExportDataType(str, Enum):
    TRACES = "traces"
    STATS = "stats"


# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------

_TRACE_FULL_COLUMNS = [
    "request_id",
    "timestamp",
    "model",
    "status",
    "input_tokens",
    "output_tokens",
    "tokens_saved",
    "cost_saved",
    "total_cost",
    "duration_ms",
    "stage_count",
    "stages_summary",
]

_TRACE_SIMPLIFIED_COLUMNS = [
    "timestamp",
    "model",
    "status",
    "tokens_saved",
    "cost_saved",
    "total_cost",
    "duration_ms",
]

_STATS_COLUMNS = ["metric", "value"]


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------


def _make_writer(buf: io.StringIO) -> Any:
    """Return a csv.writer with standard dialect (no BOM, RFC-4180-ish)."""
    return csv.writer(buf, dialect="excel", lineterminator="\r\n")


def _format_filename(ts: Optional[datetime] = None) -> str:
    """Return the standard export filename: tokenpak-export-YYYY-MM-DD-HHmmss.csv"""
    ts = ts or datetime.now()
    return f"tokenpak-export-{ts.strftime('%Y-%m-%d-%H%M%S')}.csv"


# ---------------------------------------------------------------------------
# CSVExporter
# ---------------------------------------------------------------------------


class CSVExporter:
    """Generate CSV files from tokenpak proxy data.

    Usage::

        exporter = CSVExporter(traces, session_stats)
        csv_bytes, filename = exporter.export(
            data_type=ExportDataType.TRACES,
            fmt=ExportFormat.FULL,
        )
    """

    def __init__(
        self,
        traces: Optional[List[Dict[str, Any]]] = None,
        session_stats: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._traces: List[Dict[str, Any]] = traces or []
        self._session_stats: Dict[str, Any] = session_stats or {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def export(
        self,
        data_type: ExportDataType = ExportDataType.TRACES,
        fmt: ExportFormat = ExportFormat.FULL,
        ts: Optional[datetime] = None,
    ) -> tuple[bytes, str]:
        """Generate CSV.

        Returns:
            (csv_bytes, filename) — UTF-8 encoded bytes and suggested filename.
        """
        filename = _format_filename(ts)
        buf = io.StringIO()

        if data_type == ExportDataType.TRACES:
            self._write_traces(buf, fmt)
        elif data_type == ExportDataType.STATS:
            self._write_stats(buf)
        else:
            raise ValueError(f"Unknown data_type: {data_type!r}")

        return buf.getvalue().encode("utf-8"), filename

    # ------------------------------------------------------------------
    # Internal writers
    # ------------------------------------------------------------------

    def _write_traces(self, buf: io.StringIO, fmt: ExportFormat) -> None:
        """Write trace records to buf."""
        writer = _make_writer(buf)

        if fmt == ExportFormat.FULL:
            columns = _TRACE_FULL_COLUMNS
        else:
            columns = _TRACE_SIMPLIFIED_COLUMNS

        writer.writerow(columns)

        for trace in self._traces:
            row = self._trace_to_row(trace, columns)
            writer.writerow(row)

    def _write_stats(self, buf: io.StringIO) -> None:
        """Write session stats as metric → value pairs."""
        writer = _make_writer(buf)
        writer.writerow(_STATS_COLUMNS)
        for key, value in self._session_stats.items():
            writer.writerow([key, value])

    # ------------------------------------------------------------------
    # Row transformers
    # ------------------------------------------------------------------

    def _trace_to_row(self, trace: Dict[str, Any], columns: List[str]) -> List[Any]:
        """Map a trace dict to a CSV row (in column order)."""
        stage_count = len(trace.get("stages", []))
        stages_summary = "|".join(s.get("name", "?") for s in trace.get("stages", []))

        mapping: Dict[str, Any] = {
            "request_id": trace.get("request_id", ""),
            "timestamp": trace.get("timestamp", ""),
            "model": trace.get("model", ""),
            "status": trace.get("status", ""),
            "input_tokens": trace.get("input_tokens", 0),
            "output_tokens": trace.get("output_tokens", 0),
            "tokens_saved": trace.get("tokens_saved", 0),
            "cost_saved": trace.get("cost_saved", 0.0),
            "total_cost": trace.get("total_cost", 0.0),
            "duration_ms": trace.get("duration_ms", 0.0),
            "stage_count": stage_count,
            "stages_summary": stages_summary,
        }

        return [mapping[col] for col in columns]
