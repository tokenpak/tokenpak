"""
tokenpak/agent/telemetry/export.py

Telemetry export — CSV and JSON formats with optional date-range filtering.

Usage::

    from tokenpak.agent.telemetry.export import TelemetryExporter

    exporter = TelemetryExporter(storage)

    # CSV bytes
    csv_bytes = exporter.export_csv(start="2026-03-01", end="2026-03-25")

    # JSON bytes
    json_bytes = exporter.export_json(start="2026-03-01", end="2026-03-25")

    # Suggested filename
    name = exporter.filename("csv", start="2026-03-01", end="2026-03-25")
    # → "tokenpak-telemetry-2026-03-01-2026-03-25.csv"
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .storage import TelemetryStorage

# Maximum rows returned per export to avoid memory blowout
MAX_EXPORT_ROWS = 100_000

# CSV column order
CSV_COLUMNS = [
    "request_id",
    "timestamp",
    "tokens_raw",
    "tokens_sent",
    "tokens_saved",
    "percent_saved",
    "cost_saved",
]


def _parse_date(value: Optional[str], field: str) -> Optional[str]:
    """Validate and normalise a YYYY-MM-DD date string.

    Returns an ISO-8601 datetime string suitable for SQLite comparisons, or
    raises ValueError with a helpful message.
    """
    if value is None:
        return None
    try:
        dt = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"Invalid {field} date '{value}': expected YYYY-MM-DD")
    return dt.isoformat()


class TelemetryExporter:
    """Export telemetry data from a TelemetryStorage instance.

    Parameters
    ----------
    storage:
        A :class:`TelemetryStorage` instance (or any object with a compatible
        ``query_requests`` / ``list_requests`` interface).
    """

    def __init__(self, storage: TelemetryStorage) -> None:
        self._storage = storage

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def export_csv(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
        model: Optional[str] = None,
        status: Optional[str] = None,
    ) -> bytes:
        """Return telemetry as UTF-8 CSV bytes.

        Parameters
        ----------
        start, end:
            Optional date strings in YYYY-MM-DD format.
        model:
            Filter by model name (exact match on ``model`` column if present).
        status:
            Filter by status string (``success`` / ``error``) if column present.
        """
        rows = self._fetch(start, end, model, status)
        buf = io.StringIO()
        writer = csv.DictWriter(
            buf, fieldnames=CSV_COLUMNS, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)
        return buf.getvalue().encode("utf-8")

    def export_json(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
        model: Optional[str] = None,
        status: Optional[str] = None,
    ) -> bytes:
        """Return telemetry as UTF-8 JSON bytes (pretty-printed).

        The envelope::

            {
              "meta": { "exported_at": "...", "query": {...}, "row_count": N },
              "data": [ {...}, ... ]
            }
        """
        rows = self._fetch(start, end, model, status)
        payload: Dict[str, Any] = {
            "meta": {
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "query": {
                    "start": start,
                    "end": end,
                    "model": model,
                    "status": status,
                },
                "row_count": len(rows),
            },
            "data": rows,
        }
        return json.dumps(payload, indent=2, default=str).encode("utf-8")

    def filename(
        self,
        fmt: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> str:
        """Return a safe download filename.

        Examples
        --------
        >>> exporter.filename("csv", "2026-03-01", "2026-03-25")
        'tokenpak-telemetry-2026-03-01-2026-03-25.csv'
        >>> exporter.filename("json")
        'tokenpak-telemetry-all.json'
        """
        if start and end:
            date_part = f"{start}-{end}"
        elif start:
            date_part = f"from-{start}"
        elif end:
            date_part = f"until-{end}"
        else:
            date_part = "all"
        ext = fmt.lower().strip(".")
        return f"tokenpak-telemetry-{date_part}.{ext}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch(
        self,
        start: Optional[str],
        end: Optional[str],
        model: Optional[str],
        status: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Query storage and return a list of row dicts."""
        start_iso = _parse_date(start, "start")
        end_iso = _parse_date(end, "end")

        # end date is inclusive: extend to end-of-day
        if end_iso:
            end_iso = end_iso.replace("T00:00:00", "T23:59:59")

        return self._storage.query_requests(
            start=start_iso,
            end=end_iso,
            model=model,
            status=status,
            limit=MAX_EXPORT_ROWS,
        )
