"""tokenpak.telemetry.export — telemetry export utilities."""
from __future__ import annotations

from typing import Optional

MAX_EXPORT_ROWS = 10_000


def _parse_date(ts: Optional[str]) -> str:
    """Normalise an ISO date string to YYYY-MM-DD, or return '' on failure."""
    if not ts:
        return ""
    try:
        return ts[:10]
    except Exception:
        return ""


class TelemetryExporter:
    """Export telemetry data to CSV / JSON."""

    def __init__(self, db_path: str = "", max_rows: int = MAX_EXPORT_ROWS) -> None:
        self.db_path = db_path
        self.max_rows = max_rows

    def export_csv(self, output_path: str, period: Optional[str] = None) -> int:
        raise NotImplementedError

    def export_json(self, output_path: str, period: Optional[str] = None) -> int:
        raise NotImplementedError


__all__ = ["MAX_EXPORT_ROWS", "_parse_date", "TelemetryExporter"]
