"""Anonymous Metrics — opt-in, content-free telemetry.

Collects only:
  - token counts (input, output, saved)
  - model name
  - compression ratio
  - latency_ms
  - date (UTC day bucket — no timestamp precision below day)

NO prompt content, NO response content, NO user-identifiable data.

Local store: ~/.tokenpak/metrics.db (SQLite)
Batch sync via MetricsReporter → /v1/metrics/ingest
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

METRICS_DB = Path(os.path.expanduser("~/.tokenpak/metrics.db"))

# Version tag lets the ingest endpoint evolve schemas without breakage.
SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------


@dataclass
class MetricsRecord:
    """One anonymised request record. No content fields allowed."""

    # Identity (local only; stripped before upload)
    local_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Timing — day-only bucket preserves privacy
    date_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    # Token counts
    input_tokens: int = 0
    output_tokens: int = 0
    tokens_saved: int = 0

    # Derived
    compression_ratio: float = 0.0  # tokens_saved / input_tokens

    # Performance
    latency_ms: float = 0.0

    # Routing
    model: str = ""

    # Schema version
    schema_version: str = SCHEMA_VERSION

    # Upload state (local column only — not serialised for upload)
    synced: bool = False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def to_upload_dict(self) -> dict:
        """Return a dict safe to send to the ingest endpoint (no local_id)."""
        d = asdict(self)
        # Strip local-only fields
        d.pop("local_id", None)
        d.pop("synced", None)
        return d

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "MetricsRecord":
        return cls(
            local_id=row["local_id"],
            date_utc=row["date_utc"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            tokens_saved=row["tokens_saved"],
            compression_ratio=row["compression_ratio"],
            latency_ms=row["latency_ms"],
            model=row["model"],
            schema_version=row["schema_version"],
            synced=bool(row["synced"]),
        )

    # Guard: ensure no content fields are ever added accidentally
    _ALLOWED_FIELDS = frozenset(
        {
            "local_id",
            "date_utc",
            "input_tokens",
            "output_tokens",
            "tokens_saved",
            "compression_ratio",
            "latency_ms",
            "model",
            "schema_version",
            "synced",
        }
    )

    def __post_init__(self):
        # Validate no unexpected fields snuck in
        extra = set(asdict(self)) - self._ALLOWED_FIELDS
        if extra:
            raise ValueError(f"MetricsRecord contains disallowed fields: {extra}")


# ---------------------------------------------------------------------------
# Local store
# ---------------------------------------------------------------------------


class MetricsStore:
    """SQLite-backed local metrics store."""

    def __init__(self, db_path: Path = METRICS_DB):
        self.db_path = db_path
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metrics (
                    local_id        TEXT PRIMARY KEY,
                    date_utc        TEXT NOT NULL,
                    input_tokens    INTEGER NOT NULL DEFAULT 0,
                    output_tokens   INTEGER NOT NULL DEFAULT 0,
                    tokens_saved    INTEGER NOT NULL DEFAULT 0,
                    compression_ratio REAL NOT NULL DEFAULT 0.0,
                    latency_ms      REAL NOT NULL DEFAULT 0.0,
                    model           TEXT NOT NULL DEFAULT '',
                    schema_version  TEXT NOT NULL DEFAULT '1.0',
                    synced          INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.commit()

    def record(self, rec: MetricsRecord) -> None:
        """Insert a new metrics record."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO metrics
                    (local_id, date_utc, input_tokens, output_tokens, tokens_saved,
                     compression_ratio, latency_ms, model, schema_version, synced)
                VALUES (?,?,?,?,?,?,?,?,?,0)
                """,
                (
                    rec.local_id,
                    rec.date_utc,
                    rec.input_tokens,
                    rec.output_tokens,
                    rec.tokens_saved,
                    rec.compression_ratio,
                    rec.latency_ms,
                    rec.model,
                    rec.schema_version,
                ),
            )
            conn.commit()

    def get_pending(self, limit: int = 500) -> List[MetricsRecord]:
        """Return unsynced records."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM metrics WHERE synced=0 ORDER BY date_utc LIMIT ?", (limit,)
            ).fetchall()
        return [MetricsRecord.from_row(r) for r in rows]

    def mark_synced(self, local_ids: List[str]) -> None:
        """Mark records as successfully uploaded."""
        if not local_ids:
            return
        placeholders = ",".join("?" * len(local_ids))
        with self._connect() as conn:
            conn.execute(
                f"UPDATE metrics SET synced=1 WHERE local_id IN ({placeholders})",
                local_ids,
            )
            conn.commit()

    def history(self, days: int = 30, limit: int = 500) -> List[MetricsRecord]:
        """Return all records (synced + pending) for the last N days."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM metrics
                WHERE date_utc >= date('now', ?)
                ORDER BY date_utc DESC
                LIMIT ?
                """,
                (f"-{days} days", limit),
            ).fetchall()
        return [MetricsRecord.from_row(r) for r in rows]

    def daily_summary(self, days: int = 30) -> List[dict]:
        """Aggregate stats per day for CLI display."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    date_utc,
                    COUNT(*) as requests,
                    SUM(input_tokens) as input_tokens,
                    SUM(output_tokens) as output_tokens,
                    SUM(tokens_saved) as tokens_saved,
                    AVG(compression_ratio) as avg_compression,
                    AVG(latency_ms) as avg_latency_ms,
                    SUM(CASE WHEN synced=1 THEN 1 ELSE 0 END) as synced_count
                FROM metrics
                WHERE date_utc >= date('now', ?)
                GROUP BY date_utc
                ORDER BY date_utc DESC
                """,
                (f"-{days} days",),
            ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: Optional[MetricsStore] = None


def get_store() -> MetricsStore:
    global _store
    if _store is None:
        _store = MetricsStore()
    return _store


def record_request(
    *,
    input_tokens: int,
    output_tokens: int,
    tokens_saved: int,
    latency_ms: float,
    model: str,
) -> None:
    """Record one request. No-op if metrics are disabled. Never raises."""
    try:
        from tokenpak.agent.config import get_metrics_enabled

        if not get_metrics_enabled():
            return
        compression_ratio = round(tokens_saved / input_tokens, 4) if input_tokens > 0 else 0.0
        rec = MetricsRecord(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tokens_saved=tokens_saved,
            compression_ratio=compression_ratio,
            latency_ms=round(latency_ms, 1),
            model=model,
        )
        get_store().record(rec)
    except Exception:
        pass  # telemetry must never break the proxy
