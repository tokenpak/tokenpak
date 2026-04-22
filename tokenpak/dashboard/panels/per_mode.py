"""Per-RouteClass breakdown panel for the dashboard.

Groups rows from ``monitor.db`` by the ``endpoint`` field (the proxy's
best signal for provider family pre-classifier; migrates to
``route_class`` once the monitor schema gains that column in a future
phase) and aggregates:

- Request count
- Token count (input / output / cache_read)
- Cost
- Error rate

No HTML/JS; returns pure data structures so any renderer (CLI table,
web dashboard JSON endpoint, JSON-lines exporter) can consume it.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PerModeRow:
    """One per ``route_class`` (or endpoint-family fallback)."""

    label: str
    requests: int = 0
    errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def error_rate(self) -> float:
        return self.errors / self.requests if self.requests > 0 else 0.0


@dataclass(slots=True)
class PerModePanel:
    """Dashboard panel data — per-mode breakdown over a time window."""

    rows: list[PerModeRow] = field(default_factory=list)
    since_epoch: Optional[float] = None
    until_epoch: Optional[float] = None

    @classmethod
    def load(
        cls,
        db_path: Optional[Path] = None,
        since_epoch: Optional[float] = None,
        until_epoch: Optional[float] = None,
    ) -> "PerModePanel":
        """Aggregate monitor.db rows into per-mode groups.

        Returns an empty panel (with default rows = []) if the db
        doesn't exist yet — new installs shouldn't panic the UI.
        """
        path = db_path or Path(
            os.environ.get(
                "TOKENPAK_DB",
                os.path.expanduser("~/.tokenpak/monitor.db"),
            )
        )
        if not Path(path).exists():
            return cls(rows=[], since_epoch=since_epoch, until_epoch=until_epoch)

        # Endpoint-family classification until monitor.db has a
        # route_class column. We rely on the Anthropic / OpenAI /
        # Google host identity + a catch-all `other`.
        try:
            conn = sqlite3.connect(str(path))
            query = """
                SELECT
                    CASE
                        WHEN endpoint LIKE '%anthropic%' THEN 'anthropic'
                        WHEN endpoint LIKE '%openai%' THEN 'openai'
                        WHEN endpoint LIKE '%googleapis%' THEN 'google'
                        ELSE 'other'
                    END AS label,
                    COUNT(*) AS requests,
                    SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) AS errors,
                    COALESCE(SUM(input_tokens), 0) AS input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
                    COALESCE(SUM(estimated_cost), 0.0) AS cost
                FROM requests
                WHERE 1=1
            """
            params: list = []
            if since_epoch is not None:
                query += " AND timestamp >= datetime(?, 'unixepoch')"
                params.append(since_epoch)
            if until_epoch is not None:
                query += " AND timestamp <= datetime(?, 'unixepoch')"
                params.append(until_epoch)
            query += " GROUP BY label ORDER BY requests DESC"
            rows: list[PerModeRow] = []
            for r in conn.execute(query, params):
                rows.append(
                    PerModeRow(
                        label=r[0],
                        requests=r[1] or 0,
                        errors=r[2] or 0,
                        input_tokens=r[3] or 0,
                        output_tokens=r[4] or 0,
                        cache_read_tokens=r[5] or 0,
                        cost_usd=float(r[6] or 0.0),
                    )
                )
            conn.close()
        except sqlite3.OperationalError as exc:
            # Missing table / schema drift → empty panel.
            logger.debug("PerModePanel.load: no requests table (%s)", exc)
            return cls(rows=[], since_epoch=since_epoch, until_epoch=until_epoch)

        return cls(rows=rows, since_epoch=since_epoch, until_epoch=until_epoch)

    def as_dict(self) -> dict:
        """JSON-serialisable form for API endpoints."""
        return {
            "since_epoch": self.since_epoch,
            "until_epoch": self.until_epoch,
            "rows": [
                {
                    "label": r.label,
                    "requests": r.requests,
                    "errors": r.errors,
                    "error_rate": round(r.error_rate, 4),
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens,
                    "cache_read_tokens": r.cache_read_tokens,
                    "cost_usd": round(r.cost_usd, 4),
                }
                for r in self.rows
            ],
        }


__all__ = ["PerModePanel", "PerModeRow"]
